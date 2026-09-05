from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .helpers import OcrBox
from .ocr_models import OcrError, model_root


def _create_session_without_cache(
    self: Any, onnxruntime: Any, model_path: Path, cache_path: Path, providers: Any
) -> Any:
    """替换 onnxocr 的会话创建函数，使其从不写入磁盘缓存。

    ``ORT_ENABLE_ALL`` 图优化只保留在内存中；有意不设置
    ``optimized_model_filepath`` 选项，让 onnxocr 无法持久化或重新加载
    ``cache/onnxruntime/*.optimized.onnx``。
    """

    session_options = onnxruntime.SessionOptions()
    session_options.graph_optimization_level = (
        onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
    )
    return onnxruntime.InferenceSession(
        str(model_path), sess_options=session_options, providers=providers
    )


def _cache_path_without_creation(
    model_dir: str | Path, backend: str, suffix: str
) -> Path:
    """返回与 onnxocr 相同形状的缓存路径，但不创建目录。

    onnxocr 的 ``_model_cache_path`` 辅助函数在每次引擎启动时都会顺带创建
    ``cache/<backend>`` 目录，且这发生在会话创建函数被调用之前，因此只替换
    会话创建函数仍会留下空目录。本替换函数保持调用签名不变，但完全不触碰
    文件系统；返回的路径是惰性的，因为缓存文件永远不会被视为有效。
    """
    return Path.cwd() / "cache" / backend / "never-created.optimized.onnx"


def _disable_onnxruntime_cache() -> None:
    """阻止 onnxocr 把优化后的 ONNX 模型持久化到 ``cache/onnxruntime``。

    onnxocr 的 ``PredictBase`` 会为每个模型生成一份 ORT 图优化副本
    (``cache/onnxruntime/*.optimized.onnx``) 并在后续启动时重新加载，
    且其缓存路径辅助函数会提前创建缓存目录。这里通过给
    ``onnxocr.predict_base`` 打补丁禁用该磁盘缓存：缓存文件永远不会被视为
    有效，缓存目录从不创建，会话直接从源模型创建，只做内存中的图优化。
    代价是每次引擎启动需要一次性花费数秒完成图优化。

    本调用是幂等的；未安装 onnxocr 时为空操作。
    """

    try:
        from onnxocr import predict_base
    except ImportError:
        return

    predict_base._cache_is_current = lambda _model_path, _cache_path: False
    current_path = getattr(predict_base, "_model_cache_path", None)
    if current_path is not _cache_path_without_creation:
        predict_base._model_cache_path = _cache_path_without_creation
    current = getattr(predict_base.PredictBase, "_create_onnxruntime_session", None)
    if current is not _create_session_without_cache:
        predict_base.PredictBase._create_onnxruntime_session = (
            _create_session_without_cache
        )


class OnnxOcrClient:
    """基于 onnxocr ONNX 流水线的本地 PP-OCRv5/v6 OCR。

    从本地模型目录加载设置中选择的 ``det``/``rec`` 模型，并暴露与
    自动化引擎相同的 ``recognize(image_bytes) -> list[str]`` 接口。
    """

    def __init__(
        self,
        models_directory: Path,
        det_model: str,
        rec_model: str,
        use_angle_cls: bool = False,
    ):
        self.models_directory = Path(models_directory)
        self.det_model = det_model
        self.rec_model = rec_model
        self.use_angle_cls = use_angle_cls
        self._engine: Any = None

    def models_ready(self) -> bool:
        det = self.models_directory / self.det_model
        rec = self.models_directory / self.rec_model
        return (
            (det / "inference.onnx").is_file()
            and (rec / "inference.onnx").is_file()
            and (rec / "dict.txt").is_file()
        )

    def _ensure_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        if not self.models_ready():
            raise OcrError(
                f"OCR 模型未就绪: {self.det_model} / {self.rec_model}，请在设置中下载"
            )
        try:
            from onnxocr.onnx_paddleocr import ONNXPaddleOcr
        except ImportError as exc:
            raise OcrError(
                "未安装 onnxocr，请先安装依赖: pip install onnxocr-ppocrv5 onnxruntime"
            ) from exc
        _disable_onnxruntime_cache()
        det = self.models_directory / self.det_model
        rec = self.models_directory / self.rec_model
        self._engine = ONNXPaddleOcr(
            det_model_dir=str(det / "inference.onnx"),
            rec_model_dir=str(rec / "inference.onnx"),
            rec_char_dict_path=str(rec / "dict.txt"),
            cls_model_dir="",
            use_angle_cls=self.use_angle_cls,
        )
        return self._engine

    def recognize(self, image: bytes) -> list[str]:
        return [text for text, _points in self.recognize_with_boxes(image)]

    def recognize_with_boxes(self, image: bytes) -> list[OcrBox]:
        """识别文本，并返回每条文本及其检测四边形框。

        每个四边形框是截图坐标系下四个 ``(x, y)`` 角点组成的列表，
        调用方因此可以点击文本的实际位置，而不必依赖固定坐标。
        """
        import cv2
        import numpy as np

        array = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
        if array is None:
            raise OcrError("OCR 截图解码失败")
        result = self._ensure_engine().ocr(array)
        if not result:
            return []
        boxes: list[OcrBox] = []
        for item in result[0]:
            try:
                text = str(item[1][0])
                quad = item[0]
            except (TypeError, IndexError, ValueError):
                continue
            text = text.strip()
            points: list[tuple[int, int]] = []
            if isinstance(quad, (list, tuple)):
                for point in quad:
                    try:
                        points.append(
                            (
                                round(float(point[0])),
                                round(float(point[1])),
                            )
                        )
                    except (TypeError, ValueError, IndexError):
                        continue
            if text and len(points) >= 3:
                boxes.append((text, points))
        return boxes

    def __call__(self, image: bytes) -> list[str]:
        return self.recognize(image)


def build_ocr_client(
    settings: dict[str, Any],
    base_directory: Path,
    log_callback: Callable[[str], None] | None = None,
) -> OnnxOcrClient:
    """构建本地 onnxocr 客户端；模型缺失要等到识别阶段才报错。"""

    client = OnnxOcrClient(
        model_root(settings, base_directory),
        det_model=str(settings.get("ocr_det_model", "PP-OCRv6_small_det")),
        rec_model=str(settings.get("ocr_rec_model", "PP-OCRv6_small_rec")),
    )
    if not client.models_ready() and log_callback:
        log_callback(
            f"OCR 模型未就绪: {client.det_model} / {client.rec_model}，请在设置页下载"
        )
    return client
