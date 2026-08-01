from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .helpers import OcrBox
from .ocr_models import OcrError, model_root


def _create_session_without_cache(
    self: Any, onnxruntime: Any, model_path: Path, cache_path: Path, providers: Any
) -> Any:
    """Replace onnxocr's session creator with one that never writes disk cache.

    Keeps ORT_ENABLE_ALL graph optimization in memory only; the
    optimized_model_filepath option is intentionally not set, so onnxocr
    cannot persist or reload cache/onnxruntime/*.optimized.onnx.
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
    """Return onnxocr's cache path shape without creating the directory.

    onnxocr's _model_cache_path mkdirs cache/<backend> as a side effect on
    every engine start, before the session creator is even consulted, so
    patching only the session creator would still leave an empty directory
    behind. This replacement keeps the call signature but never touches the
    filesystem; the returned path is inert because cached files are also
    never considered valid.
    """
    return Path.cwd() / "cache" / backend / "never-created.optimized.onnx"


def _disable_onnxruntime_cache() -> None:
    """Stop onnxocr from persisting optimized ONNX models under cache/onnxruntime.

    onnxocr's PredictBase builds an ORT graph-optimized copy of each model
    (cache/onnxruntime/*.optimized.onnx) and reloads it on later starts,
    and its cache path helper creates the cache directory up front. That
    on-disk cache is disabled here by patching onnxocr.predict_base:
    cached files are never considered valid, the cache directory is never
    created, and sessions are created straight from the source model with
    in-memory graph optimization only. The trade-off is a one-time graph
    optimization of a few seconds per engine start.

    The call is idempotent and a no-op when onnxocr is not installed.
    """

    try:
        from onnxocr import predict_base
    except ImportError:
        return

    predict_base._cache_is_current = lambda _model_path, _cache_path: False
    current_path = getattr(predict_base, "_model_cache_path", None)
    if current_path is not _cache_path_without_creation:
        predict_base._model_cache_path = _cache_path_without_creation
    current = getattr(
        predict_base.PredictBase, "_create_onnxruntime_session", None
    )
    if current is not _create_session_without_cache:
        predict_base.PredictBase._create_onnxruntime_session = (
            _create_session_without_cache
        )


class OnnxOcrClient:
    """Local PP-OCRv5/v6 OCR through the onnxocr ONNX pipeline.

    Loads the det/rec models selected in the settings from the local model
    directory and exposes the same recognize(image_bytes) -> list[str]
    interface used by the automation engine.
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
        """Recognize texts and return each text with its detection quad.

        Each quad is a list of four (x, y) corner points in screenshot
        coordinates, so callers can tap the actual text position instead of
        relying on fixed coordinates.
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
                                int(round(float(point[0]))),
                                int(round(float(point[1]))),
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
    """Build the local onnxocr client; missing models fail at recognition time."""

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
