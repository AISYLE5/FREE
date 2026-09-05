from __future__ import annotations

import io
import shutil
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Self
from unittest.mock import patch

from free_app.ocr_models import (
    DET_MODELS,
    MODEL_SOURCES,
    PP_OCR_MODELS,
    REC_MODELS,
    SOURCE_KEYS,
    DownloadCancelled,
    OcrError,
    _dict_file_name,
    _download_dict,
    _download_file,
    _extract_tar,
    delete_model,
    download_model,
    installed_models,
    model_root,
    resolve_download_sources,
)
from free_app.trash import TrashError


def _model_tar_bytes() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, content in (
            ("PP-OCRv6_small_det_onnx_infer/inference.onnx", b"model-bytes"),
            ("PP-OCRv6_small_det_onnx_infer/inference.yml", b"option: 1\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class OcrModelsTests(unittest.TestCase):
    def test_catalog_has_v5_and_v6_models_with_kind_url_and_size(self) -> None:
        self.assertEqual(
            set(PP_OCR_MODELS),
            {
                "PP-OCRv6_medium_det",
                "PP-OCRv6_small_det",
                "PP-OCRv6_tiny_det",
                "PP-OCRv5_server_det",
                "PP-OCRv5_mobile_det",
                "PP-OCRv6_medium_rec",
                "PP-OCRv6_small_rec",
                "PP-OCRv6_tiny_rec",
                "PP-OCRv5_server_rec",
                "PP-OCRv5_mobile_rec",
            },
        )
        self.assertEqual(
            tuple(DET_MODELS),
            (
                "PP-OCRv6_medium_det",
                "PP-OCRv6_small_det",
                "PP-OCRv6_tiny_det",
                "PP-OCRv5_server_det",
                "PP-OCRv5_mobile_det",
            ),
        )
        self.assertEqual(
            tuple(REC_MODELS),
            (
                "PP-OCRv6_medium_rec",
                "PP-OCRv6_small_rec",
                "PP-OCRv6_tiny_rec",
                "PP-OCRv5_server_rec",
                "PP-OCRv5_mobile_rec",
            ),
        )
        for name, info in PP_OCR_MODELS.items():
            self.assertIn(info.kind, {"det", "rec"})
            self.assertTrue(info.url.endswith(f"{name}_onnx_infer.tar"))
            self.assertGreater(info.size_mb, 0)
            if info.kind == "rec":
                self.assertIn(info.dict_file, {"ppocrv6_dict.txt", "ppocrv5_dict.txt"})
            else:
                self.assertIsNone(info.dict_file)

    def test_model_sources_are_registered_with_labels_and_layouts(self) -> None:
        self.assertEqual(set(MODEL_SOURCES), {"baidu", "modelscope", "huggingface"})
        for descriptor in MODEL_SOURCES.values():
            self.assertTrue(descriptor["label"])
            self.assertIn(descriptor["layout"], {"tar", "files"})

    def test_resolve_download_sources_auto_and_explicit(self) -> None:
        self.assertEqual(resolve_download_sources("auto"), SOURCE_KEYS)
        self.assertEqual(resolve_download_sources("baidu"), ("baidu",))
        with self.assertRaisesRegex(OcrError, "未知 OCR 下载源"):
            resolve_download_sources("invalid")

    def test_installed_models_detects_inference_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_dir = root / "PP-OCRv6_small_det"
            model_dir.mkdir(parents=True)
            (model_dir / "inference.onnx").write_bytes(b"m")

            installed = installed_models(root)

            self.assertTrue(installed["PP-OCRv6_small_det"])
            self.assertFalse(installed["PP-OCRv6_tiny_det"])
            self.assertFalse(installed["PP-OCRv6_small_rec"])

    def test_download_model_extracts_and_validates_tar(self) -> None:
        tar_bytes = _model_tar_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "free_app.ocr_models._download_file",
                side_effect=lambda _url, destination, _callback, _cancel_event=None: (
                    destination.write_bytes(tar_bytes)
                ),
            ):
                target = download_model("PP-OCRv6_small_det", root)

            self.assertEqual(target, root / "PP-OCRv6_small_det")
            self.assertTrue((target / "inference.onnx").is_file())
            self.assertTrue((target / "inference.yml").is_file())
            self.assertFalse((root / "PP-OCRv6_small_det_infer.tar").exists())
            self.assertTrue(installed_models(root)["PP-OCRv6_small_det"])

    def test_download_model_rejects_tar_without_inference_model(self) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo("only.txt")
            content = b"hello"
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "free_app.ocr_models._download_file",
                side_effect=lambda _url, destination, _callback, _cancel_event=None: (
                    destination.write_bytes(buffer.getvalue())
                ),
            ):
                with self.assertRaises(OcrError):
                    download_model("PP-OCRv6_small_det", root, source="baidu")
            self.assertFalse((root / "PP-OCRv6_small_det").exists())

    def test_download_model_cancel_raises_and_cleans_tar(self) -> None:
        event = threading.Event()

        class ChunkedResponse:
            headers = {"Content-Length": "1024"}

            def __enter__(self) -> Self:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self, size: int) -> bytes:
                event.set()
                return b"x" * min(size, 512)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch(
                    "free_app.ocr_models.urllib.request.urlopen",
                    side_effect=lambda *_args, **_kwargs: ChunkedResponse(),
                ),
                self.assertRaises(DownloadCancelled),
            ):
                download_model("PP-OCRv6_small_det", root, cancel_event=event)
            self.assertFalse((root / "PP-OCRv6_small_det_infer.tar").exists())

    def test_download_file_reports_progress_and_retries_transient_error(self) -> None:
        class Response:
            headers = {"Content-Length": "3"}

            def __init__(self) -> None:
                self.chunks = iter((b"abc", b""))

            def __enter__(self) -> Self:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self, _size: int) -> bytes:
                return next(self.chunks)

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "model.tar"
            progress: list[tuple[int, int]] = []
            with patch(
                "free_app.ocr_models.urllib.request.urlopen",
                side_effect=[OSError("temporary network error"), Response()],
            ) as urlopen:
                _download_file(
                    "https://example.invalid/model",
                    destination,
                    lambda received, total: progress.append((received, total)),
                )

            self.assertEqual(destination.read_bytes(), b"abc")
            self.assertEqual(progress, [(3, 3)])
            self.assertEqual(urlopen.call_count, 2)

    def test_download_file_rejects_exhausted_or_empty_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "model.tar"
            with (
                patch(
                    "free_app.ocr_models.urllib.request.urlopen",
                    side_effect=[OSError("first"), OSError("second")],
                ),
                self.assertRaises(OcrError),
            ):
                _download_file("https://example.invalid/model", destination, None)

            class EmptyResponse:
                headers = {}

                def __enter__(self) -> Self:
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def read(self, _size: int) -> bytes:
                    return b""

            with patch(
                "free_app.ocr_models.urllib.request.urlopen",
                return_value=EmptyResponse(),
            ):
                with self.assertRaisesRegex(OcrError, "结果为空"):
                    _download_file("https://example.invalid/empty", destination, None)

    def test_download_dict_falls_back_to_second_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "dict.txt"
            with patch(
                "free_app.ocr_models._download_file",
                side_effect=[OcrError("primary failed"), None],
            ) as download:
                _download_dict("ppocrv6_dict.txt", target)

        self.assertEqual(download.call_count, 2)
        self.assertEqual(download.call_args.args[0].split("/")[2], "gitee.com")

    def test_download_dict_reports_failure_when_all_sources_fail(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "free_app.ocr_models._download_file",
                side_effect=OcrError("unavailable"),
            ),
            self.assertRaises(OcrError),
        ):
            _download_dict("ppocrv6_dict.txt", Path(directory) / "dict.txt")

    def test_download_model_auto_falls_back_to_next_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempted: list[str] = []

            def fake_download(
                info,
                source,
                target,
                tar_path,
                progress_callback=None,
                cancel_event=None,
            ) -> None:
                attempted.append(source)
                if source == "baidu":
                    raise OcrError("primary source unavailable")

            with (
                patch(
                    "free_app.ocr_models._download_model_from_source",
                    side_effect=fake_download,
                ),
                patch("free_app.ocr_models._is_installed", side_effect=[False, True]),
            ):
                result = download_model("PP-OCRv6_small_det", root)

            self.assertEqual(result, root / "PP-OCRv6_small_det")
            self.assertEqual(attempted, ["baidu", "modelscope"])

    def test_download_model_reports_failure_when_all_sources_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def fake_download(
                info,
                source,
                target,
                tar_path,
                progress_callback=None,
                cancel_event=None,
            ) -> None:
                raise OcrError(f"source {source} unavailable")

            with (
                patch(
                    "free_app.ocr_models._download_model_from_source",
                    side_effect=fake_download,
                ),
                patch("free_app.ocr_models._is_installed", side_effect=[False]),
            ):
                with self.assertRaisesRegex(OcrError, "OCR 模型下载失败"):
                    download_model("PP-OCRv6_small_det", root)

    def test_download_model_uses_only_selected_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempted: list[str] = []

            def fake_download(
                info,
                source,
                target,
                tar_path,
                progress_callback=None,
                cancel_event=None,
            ) -> None:
                attempted.append(source)

            with (
                patch(
                    "free_app.ocr_models._download_model_from_source",
                    side_effect=fake_download,
                ),
                patch("free_app.ocr_models._is_installed", side_effect=[False, True]),
            ):
                download_model("PP-OCRv6_small_det", root, source="modelscope")

            self.assertEqual(attempted, ["modelscope"])

    def test_download_model_rejects_unknown_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("free_app.ocr_models._download_model_from_source"):
                with self.assertRaisesRegex(OcrError, "未知 OCR 下载源"):
                    download_model("PP-OCRv6_small_det", root, source="invalid")

    def test_download_model_files_source_writes_onnx_and_yml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def fake_download(
                url, destination, progress_callback=None, cancel_event=None
            ) -> None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"file-bytes")

            with patch("free_app.ocr_models._download_file", side_effect=fake_download):
                target = download_model(
                    "PP-OCRv6_small_det", root, source="huggingface"
                )

            self.assertEqual(target, root / "PP-OCRv6_small_det")
            self.assertTrue((target / "inference.onnx").is_file())
            self.assertTrue((target / "inference.yml").is_file())

    def test_download_model_files_source_fetches_dict_for_rec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def fake_download(
                url, destination, progress_callback=None, cancel_event=None
            ) -> None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"file-bytes")

            with (
                patch("free_app.ocr_models._download_file", side_effect=fake_download),
                patch(
                    "free_app.ocr_models._download_dict",
                    side_effect=lambda dict_file, target, cancel_event=None: (
                        target.write_text("dict")
                    ),
                ) as download_dict,
            ):
                target = download_model("PP-OCRv6_small_rec", root, source="modelscope")

            download_dict.assert_called_once()
            self.assertTrue((target / "dict.txt").is_file())

    def test_download_model_skips_network_when_model_is_installed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "PP-OCRv6_small_det"
            target.mkdir(parents=True)
            (target / "inference.onnx").write_bytes(b"model")
            with patch("free_app.ocr_models._download_file") as download:
                self.assertEqual(download_model("PP-OCRv6_small_det", root), target)

            download.assert_not_called()

    def test_delete_model_sends_to_recycle_bin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "models"
            model_dir = root / "PP-OCRv6_small_rec"
            model_dir.mkdir(parents=True)
            (model_dir / "inference.pdmodel").write_bytes(b"m")

            def fake_trash(path: Path) -> None:
                shutil.rmtree(path)

            with patch(
                "free_app.ocr_models.send_to_recycle_bin", side_effect=fake_trash
            ) as trash:
                delete_model("PP-OCRv6_small_rec", root)

            trash.assert_called_once_with(model_dir)
            self.assertFalse(model_dir.exists())

    def test_delete_model_reports_recycle_bin_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_dir = root / "PP-OCRv6_small_rec"
            model_dir.mkdir()
            with (
                patch(
                    "free_app.ocr_models.send_to_recycle_bin",
                    side_effect=TrashError("发送到回收站失败"),
                ),
                self.assertRaisesRegex(OcrError, "回收站"),
            ):
                delete_model("PP-OCRv6_small_rec", root)

    def test_model_root_resolves_relative_and_absolute_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self.assertEqual(
                model_root({"ocr_model_directory": "models"}, base), base / "models"
            )
            absolute = base / "custom" / "models"
            self.assertEqual(
                model_root({"ocr_model_directory": str(absolute)}, base), absolute
            )

    def test_dict_file_name_maps_v5_and_v6_recognizers(self) -> None:
        self.assertEqual(_dict_file_name("PP-OCRv5_mobile_rec"), "ppocrv5_dict.txt")
        self.assertEqual(_dict_file_name("PP-OCRv6_small_rec"), "ppocrv6_dict.txt")
        self.assertIsNone(_dict_file_name("other"))

    def test_installed_rec_model_requires_dict_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_dir = root / "PP-OCRv6_small_rec"
            model_dir.mkdir(parents=True)
            (model_dir / "inference.onnx").write_bytes(b"model")

            installed = installed_models(root)

        self.assertFalse(installed["PP-OCRv6_small_rec"])

    def test_extract_tar_replaces_staging_and_target(self) -> None:
        tar_path = Path(tempfile.mkdtemp()) / "model.tar"
        tar_path.write_bytes(_model_tar_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / ".PP-OCRv6_small_det_staging"
            staging.mkdir()
            target = root / "PP-OCRv6_small_det"
            target.mkdir()
            (target / "inference.yml").write_text("old", encoding="utf-8")

            _extract_tar(tar_path, target)

            self.assertTrue((target / "inference.onnx").is_file())
            self.assertNotEqual(
                (target / "inference.yml").read_text(encoding="utf-8"), "old"
            )
            self.assertFalse(staging.exists())

    def test_extract_tar_rejects_missing_inference_model(self) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo("only.txt")
            content = b"hello"
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        with tempfile.TemporaryDirectory() as directory:
            tar_path = Path(directory) / "model.tar"
            tar_path.write_bytes(buffer.getvalue())

            with self.assertRaisesRegex(OcrError, "inference.onnx"):
                _extract_tar(tar_path, Path(directory) / "target")

    def test_download_model_rejects_unknown_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OcrError):
                download_model("not-a-model", Path(directory))

    def test_delete_model_rejects_unknown_or_missing_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(OcrError, "未知 OCR 模型"):
                delete_model("not-a-model", root)
            with self.assertRaisesRegex(OcrError, "未下载"):
                delete_model("PP-OCRv6_small_det", root)

    def test_delete_model_permanent_removes_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_dir = root / "PP-OCRv6_small_det"
            model_dir.mkdir()
            (model_dir / "inference.onnx").write_bytes(b"m")

            delete_model("PP-OCRv6_small_det", root, mode="permanent")

            self.assertFalse(model_dir.exists())

    def test_delete_model_raises_when_recycle_bin_keeps_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_dir = root / "PP-OCRv6_small_det"
            model_dir.mkdir()

            with (
                patch(
                    "free_app.ocr_models.send_to_recycle_bin",
                    side_effect=lambda _path: None,
                ),
                self.assertRaisesRegex(OcrError, "未能移除"),
            ):
                delete_model("PP-OCRv6_small_det", root)


if __name__ == "__main__":
    unittest.main()
