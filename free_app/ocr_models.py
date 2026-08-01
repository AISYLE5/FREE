from __future__ import annotations

import shutil
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import OCR_DOWNLOAD_SOURCES, resolve_path
from .trash import TrashError, send_to_recycle_bin


class OcrError(RuntimeError):
    pass


class DownloadCancelled(RuntimeError):
    pass


MODEL_BASE_URL = "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0"
DICT_SOURCES = (
    "https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/ppocr/utils/dict",
    "https://gitee.com/paddlepaddle/PaddleOCR/raw/main/ppocr/utils/dict",
)
AUTO_SOURCE = "auto"
SOURCE_KEYS = tuple(key for key in OCR_DOWNLOAD_SOURCES if key != AUTO_SOURCE)
DOWNLOAD_ATTEMPTS = 2

MODEL_SOURCES: dict[str, dict[str, str]] = {
    "baidu": {
        "label": "百度 BCE",
        "layout": "tar",
        "base": MODEL_BASE_URL,
    },
    "modelscope": {
        "label": "ModelScope",
        "layout": "files",
        "base": "https://www.modelscope.cn/models/PaddlePaddle",
        "branch": "master",
    },
    "huggingface": {
        "label": "HuggingFace",
        "layout": "files",
        "base": "https://huggingface.co/PaddlePaddle",
        "branch": "main",
    },
}


@dataclass(frozen=True)
class OcrModelInfo:
    name: str
    kind: str
    size_mb: float
    url: str
    dict_file: str | None = None


def _dict_file_name(name: str) -> str | None:
    if "v6" in name:
        return "ppocrv6_dict.txt"
    if "v5" in name:
        return "ppocrv5_dict.txt"
    return None


PP_OCR_MODELS: dict[str, OcrModelInfo] = {
    name: OcrModelInfo(
        name=name,
        kind=kind,
        size_mb=size_mb,
        url=f"{MODEL_BASE_URL}/{name}_onnx_infer.tar",
        dict_file=_dict_file_name(name) if kind == "rec" else None,
    )
    for name, kind, size_mb in (
        ("PP-OCRv6_medium_det", "det", 59.17),
        ("PP-OCRv6_small_det", "det", 9.43),
        ("PP-OCRv6_tiny_det", "det", 1.71),
        ("PP-OCRv5_server_det", "det", 84.05),
        ("PP-OCRv5_mobile_det", "det", 4.62),
        ("PP-OCRv6_medium_rec", "rec", 73.16),
        ("PP-OCRv6_small_rec", "rec", 20.33),
        ("PP-OCRv6_tiny_rec", "rec", 4.32),
        ("PP-OCRv5_server_rec", "rec", 80.75),
        ("PP-OCRv5_mobile_rec", "rec", 15.93),
    )
}

DET_MODELS = tuple(name for name, info in PP_OCR_MODELS.items() if info.kind == "det")
REC_MODELS = tuple(name for name, info in PP_OCR_MODELS.items() if info.kind == "rec")

ProgressCallback = Callable[[int, int], None]


def model_root(settings: dict[str, Any], base_directory: Path) -> Path:
    """Resolve the local directory that holds downloaded OCR models."""

    return resolve_path(settings.get("ocr_model_directory"), base_directory)


def _is_installed(directory: Path, kind: str | None = None) -> bool:
    """ONNX inference model plus the dict file required by recognizers."""

    if not (directory / "inference.onnx").is_file():
        return False
    if kind == "rec" and not (directory / "dict.txt").is_file():
        return False
    return True


def installed_models(root: Path) -> dict[str, bool]:
    """Return which catalog models are already downloaded under root."""

    return {name: _is_installed(root / name, info.kind) for name, info in PP_OCR_MODELS.items()}


def _retry_download(
    steps: Iterable[Any],
    step: Callable[[Any], None],
    error_factory: Callable[[Exception | None], Exception],
) -> None:
    """Run ``step`` over ``steps`` until one succeeds.

    A cancelled download is always re-raised; any other failure records the
    last error and moves on. If no step succeeds, ``error_factory`` builds the
    exception to raise (receiving the last recorded error, or ``None``).
    """

    last_error: Exception | None = None
    for item in steps:
        try:
            step(item)
            return
        except DownloadCancelled:
            raise
        except Exception as exc:
            last_error = exc
    raise error_factory(last_error)


def _download_file(
    url: str,
    destination: Path,
    progress_callback: ProgressCallback | None,
    cancel_event: Any | None = None,
) -> None:
    def _attempt() -> None:
        request = urllib.request.Request(url)
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            with destination.open("wb") as handle:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise DownloadCancelled("用户取消下载")
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    if progress_callback:
                        progress_callback(received, total)

    _retry_download(
        range(DOWNLOAD_ATTEMPTS),
        lambda _number: _attempt(),
        lambda error: OcrError(f"OCR 模型下载失败: {error}"),
    )
    try:
        size = destination.stat().st_size
    except OSError:
        size = 0
    if size == 0:
        raise OcrError("OCR 模型下载结果为空")


def _download_dict(dict_file: str, target: Path, cancel_event: Any | None = None) -> None:
    _retry_download(
        DICT_SOURCES,
        lambda source: _download_file(
            f"{source}/{dict_file}", target, None, cancel_event
        ),
        lambda error: OcrError(f"OCR 字典下载失败: {dict_file} ({error})"),
    )


def resolve_download_sources(source: str) -> tuple[str, ...]:
    """Expand a requested source into the ordered list of sources to try."""

    if source == AUTO_SOURCE:
        return SOURCE_KEYS
    if source not in MODEL_SOURCES:
        raise OcrError(f"未知 OCR 下载源: {source}")
    return (source,)


def _download_model_from_source(
    info: OcrModelInfo,
    source: str,
    target: Path,
    tar_path: Path,
    progress_callback: ProgressCallback | None,
    cancel_event: Any | None,
) -> None:
    """Fetch a model from one mirror source into target."""

    descriptor = MODEL_SOURCES[source]
    if descriptor["layout"] == "tar":
        _download_file(info.url, tar_path, progress_callback, cancel_event)
        _extract_tar(tar_path, target)
        return
    target.mkdir(parents=True, exist_ok=True)
    repo = f"{info.name}_onnx"
    for filename in ("inference.onnx", "inference.yml"):
        url = f"{descriptor['base']}/{repo}/resolve/{descriptor['branch']}/{filename}"
        _download_file(url, target / filename, progress_callback, cancel_event)


def _extract_tar(tar_path: Path, target: Path) -> None:
    staging = target.parent / f".{target.name}_staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(tar_path, "r:*") as archive:
            archive.extractall(staging, filter="data")
        model_files = list(staging.rglob("inference.onnx"))
        if not model_files:
            raise OcrError("模型压缩包内缺少 inference.onnx")
        source_dir = model_files[0].parent
        target.mkdir(parents=True, exist_ok=True)
        for item in source_dir.iterdir():
            destination = target / item.name
            if destination.exists():
                if destination.is_dir():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            shutil.move(str(item), str(destination))
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        tar_path.unlink(missing_ok=True)


def download_model(
    name: str,
    root: Path,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Any | None = None,
    source: str = AUTO_SOURCE,
) -> Path:
    """Download and extract an inference model into root/name.

    ``source`` names a single mirror from MODEL_SOURCES, or "auto" to try
    every source in order until one succeeds.
    """

    info = PP_OCR_MODELS.get(name)
    if info is None:
        raise OcrError(f"未知 OCR 模型: {name}")
    target = root / name
    if _is_installed(target, info.kind):
        return target
    sources = resolve_download_sources(source)
    root.mkdir(parents=True, exist_ok=True)
    tar_path = root / f"{name}_infer.tar"
    try:
        _retry_download(
            sources,
            lambda source_key: _download_model_from_source(
                info,
                source_key,
                target,
                tar_path,
                progress_callback,
                cancel_event,
            ),
            lambda error: OcrError(f"OCR 模型下载失败: {name} ({error})"),
        )
        if info.dict_file:
            _download_dict(info.dict_file, target / "dict.txt", cancel_event)
    except Exception:
        tar_path.unlink(missing_ok=True)
        raise
    if not _is_installed(target):
        raise OcrError(f"OCR 模型 {name} 下载后不完整")
    return target


def delete_model(name: str, root: Path, mode: str = "recycle") -> Path:
    """Remove a downloaded model directory.

    ``mode`` is "recycle" (Windows Recycle Bin, the default) or "permanent".
    """

    if name not in PP_OCR_MODELS:
        raise OcrError(f"未知 OCR 模型: {name}")
    source = root / name
    if not source.is_dir():
        raise OcrError(f"OCR 模型未下载: {name}")
    if mode == "permanent":
        shutil.rmtree(source)
        return source
    try:
        send_to_recycle_bin(source)
    except TrashError as exc:
        raise OcrError(str(exc)) from exc
    if source.exists():
        raise OcrError(f"OCR 模型未能移除（回收站拒绝）: {name}")
    return source
