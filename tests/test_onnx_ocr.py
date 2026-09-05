from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
from free_app.ocr_models import OcrError
from free_app.onnx_ocr import (
    OnnxOcrClient,
    _disable_onnxruntime_cache,
    build_ocr_client,
)


class OnnxOcrTests(unittest.TestCase):
    @staticmethod
    def _write_ready_models(root: Path) -> tuple[Path, Path]:
        det = root / "PP-OCRv6_small_det"
        rec = root / "PP-OCRv6_small_rec"
        det.mkdir(parents=True)
        rec.mkdir(parents=True)
        (det / "inference.onnx").write_bytes(b"model")
        (rec / "inference.onnx").write_bytes(b"model")
        (rec / "dict.txt").write_text("dict", encoding="utf-8")
        return det, rec

    def test_models_ready_requires_onnx_and_dict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            det = root / "PP-OCRv6_small_det"
            rec = root / "PP-OCRv6_small_rec"
            det.mkdir(parents=True)
            rec.mkdir(parents=True)
            client = OnnxOcrClient(root, "PP-OCRv6_small_det", "PP-OCRv6_small_rec")
            self.assertFalse(client.models_ready())
            (det / "inference.onnx").write_bytes(b"x")
            (rec / "inference.onnx").write_bytes(b"x")
            self.assertFalse(client.models_ready())
            (rec / "dict.txt").write_text("dict", encoding="utf-8")
            self.assertTrue(client.models_ready())

    def test_recognize_raises_when_models_missing(self) -> None:
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        _, encoded = cv2.imencode(".png", image)
        with tempfile.TemporaryDirectory() as directory:
            client = OnnxOcrClient(
                Path(directory), "PP-OCRv6_small_det", "PP-OCRv6_small_rec"
            )
            with self.assertRaisesRegex(OcrError, "模型未就绪"):
                client.recognize(encoded.tobytes())

    def test_recognize_with_boxes_normalizes_valid_results(self) -> None:
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        _, encoded = cv2.imencode(".png", image)
        engine = MagicMock()
        engine.ocr.return_value = [
            [
                (
                    [[0.4, 1.6], [10.6, 1.4], [10.4, 9.6], [0.6, 9.4]],
                    ("  result  ", 0.99),
                ),
                None,
                ([[1, 1], [2, 2]], ("too short", 0.8)),
                ([[1, 1], [2, 2], [3, 3]], ("   ", 0.7)),
            ]
        ]
        with tempfile.TemporaryDirectory() as directory:
            client = OnnxOcrClient(Path(directory), "det", "rec")
            client._engine = engine

            boxes = client.recognize_with_boxes(encoded.tobytes())
            texts = client.recognize(encoded.tobytes())

        self.assertEqual(
            boxes,
            [("result", [(0, 2), (11, 1), (10, 10), (1, 9)])],
        )
        self.assertEqual(texts, ["result"])
        self.assertEqual(engine.ocr.call_count, 2)

    def test_recognize_rejects_invalid_image_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = OnnxOcrClient(Path(directory), "det", "rec")

            with self.assertRaises(OcrError):
                client.recognize(b"not-an-image")

    def test_ensure_engine_initializes_once_with_selected_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            det, rec = self._write_ready_models(root)
            engine = object()
            constructor = MagicMock(return_value=engine)
            package = types.ModuleType("onnxocr")
            package.__path__ = []
            module = types.ModuleType("onnxocr.onnx_paddleocr")
            module.ONNXPaddleOcr = constructor
            client = OnnxOcrClient(
                root,
                det.name,
                rec.name,
                use_angle_cls=True,
            )

            with patch.dict(
                sys.modules,
                {"onnxocr": package, "onnxocr.onnx_paddleocr": module},
            ):
                self.assertIs(client._ensure_engine(), engine)
                self.assertIs(client._ensure_engine(), engine)

        constructor.assert_called_once_with(
            det_model_dir=str(det / "inference.onnx"),
            rec_model_dir=str(rec / "inference.onnx"),
            rec_char_dict_path=str(rec / "dict.txt"),
            cls_model_dir="",
            use_angle_cls=True,
        )

    def test_build_uses_onnxocr_when_models_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_ready_models(root)
            settings = {"ocr_model_directory": str(root)}
            callback = build_ocr_client(settings, Path("."))
            self.assertIsInstance(callback, OnnxOcrClient)
            self.assertTrue(callback.models_ready())

    def test_build_returns_onnx_client_when_models_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = {
                "ocr_model_directory": str(Path(directory) / "models"),
            }
            callback = build_ocr_client(settings, Path(directory))
            self.assertIsInstance(callback, OnnxOcrClient)

    def test_disable_onnxruntime_cache_removes_cache_paths(self) -> None:
        class FakePredictBase:
            _create_onnxruntime_session = None

        fake_predict_base = types.ModuleType("onnxocr.predict_base")
        fake_predict_base.PredictBase = FakePredictBase
        fake_predict_base._cache_is_current = lambda model_path, cache_path: True
        package = types.ModuleType("onnxocr")
        package.__path__ = []

        with patch.dict(
            sys.modules,
            {"onnxocr": package, "onnxocr.predict_base": fake_predict_base},
        ):
            _disable_onnxruntime_cache()

            # 已缓存的模型不再被视为有效。
            self.assertFalse(
                fake_predict_base._cache_is_current(Path("model"), Path("cache"))
            )

            # 创建会话时保留内存内优化，但绝不设置
            # optimized_model_filepath，因此不会向磁盘写任何内容。
            onnxruntime = MagicMock()
            onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL = 99
            onnxruntime.SessionOptions.return_value = options = types.SimpleNamespace()
            onnxruntime.InferenceSession.return_value = session = MagicMock()
            result = FakePredictBase._create_onnxruntime_session(
                object(),
                onnxruntime,
                Path("models/det/inference.onnx"),
                Path("cache/onnxruntime/det.optimized.onnx"),
                ["CPUExecutionProvider"],
            )
            self.assertIs(result, session)
            self.assertEqual(
                options.graph_optimization_level,
                99,
            )
            self.assertFalse(hasattr(options, "optimized_model_filepath"))
            onnxruntime.InferenceSession.assert_called_once_with(
                str(Path("models/det/inference.onnx")),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )

            # 缓存路径辅助函数不得创建缓存目录。
            with tempfile.TemporaryDirectory() as directory:
                cache_root = Path(directory)
                with patch("free_app.onnx_ocr.Path.cwd", return_value=cache_root):
                    path = fake_predict_base._model_cache_path(
                        "models/det/inference.onnx",
                        "onnxruntime",
                        ".optimized.onnx",
                    )
                    assert_path = cache_root / "cache" / "onnxruntime"
                    self.assertEqual(
                        path,
                        assert_path / "never-created.optimized.onnx",
                    )
                    self.assertFalse(assert_path.exists())

            # 幂等：第二次调用仍保留同样的替换方法。
            original = FakePredictBase._create_onnxruntime_session
            path_helper = fake_predict_base._model_cache_path
            _disable_onnxruntime_cache()
            self.assertIs(FakePredictBase._create_onnxruntime_session, original)
            self.assertIs(fake_predict_base._model_cache_path, path_helper)

    def test_disable_onnxruntime_cache_noop_without_onnxocr(self) -> None:
        package = types.ModuleType("onnxocr")
        package.__path__ = []
        modules = dict(sys.modules)
        modules.pop("onnxocr.predict_base", None)
        modules["onnxocr"] = package
        with patch.dict(sys.modules, modules):
            # 不存在 onnxocr.predict_base：补丁不得抛异常，
            # 也不得改动其他任何东西。
            _disable_onnxruntime_cache()
        # 补丁桩已再次消失：没有泄漏到真实模块中。
        self.assertNotIn("onnxocr.predict_base", sys.modules)


if __name__ == "__main__":
    unittest.main()
