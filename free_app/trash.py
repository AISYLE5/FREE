from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from .helpers import LogCallback, noop_log


class TrashError(RuntimeError):
    pass


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", ctypes.c_uint),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


_FO_DELETE = 0x0003
_FOF_ALLOWUNDO = 0x0040
_FOF_NOCONFIRMATION = 0x0010
_FOF_SILENT = 0x0004
_FOF_NOERRORUI = 0x0400


def remove_path(path: Path, mode: str) -> None:
    """按清理模式删除文件：``permanent`` 直接删除，其余方式发送到回收站。"""

    if mode == "permanent":
        path.unlink()
    else:
        send_to_recycle_bin(path)


def send_to_recycle_bin(path: Path) -> None:
    """将文件或文件夹移动到 Windows 回收站（可恢复）。"""

    if os.name != "nt":
        raise TrashError("仅 Windows 支持发送到回收站")
    source = str(path.resolve())
    from_buffer = ctypes.create_unicode_buffer(source + "\0\0")
    operation = _SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = _FO_DELETE
    operation.pFrom = ctypes.cast(from_buffer, wintypes.LPCWSTR)
    operation.pTo = None
    operation.fFlags = (
        _FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT | _FOF_NOERRORUI
    )
    operation.fAnyOperationsAborted = False
    operation.hNameMappings = None
    operation.lpszProgressTitle = None
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0:
        raise TrashError(f"发送到回收站失败，系统返回错误码: {result}")


def prune_files(
    directory: Path,
    max_files: int,
    mode: str,
    log_callback: LogCallback | None = None,
) -> int:
    """按清理模式保留最新的 ``max_files`` 个文件并删除更旧的文件。"""

    log = noop_log(log_callback)
    if max_files <= 0 or not directory.is_dir():
        return 0
    files = sorted(
        (path for path in directory.iterdir() if path.is_file()),
        key=lambda path: path.stat().st_mtime,
    )
    if len(files) <= max_files:
        return 0
    to_remove = files[: len(files) - max_files]
    removed = _remove_files(
        to_remove, mode, log, message_prefix="清理旧文件", exceed_limit=True
    )
    return removed


def clear_output_files(
    directory: Path,
    mode: str,
    log_callback: LogCallback | None = None,
) -> int:
    """用配置的清理模式删除 ``directory`` 下的所有文件。

    ``recycle`` 将每个文件移入 Windows 回收站；``permanent``
    立即删除。这是手动“清理全部日志/截图”操作。
    """

    log = noop_log(log_callback)
    if not directory.is_dir():
        return 0
    files = [path for path in directory.rglob("*") if path.is_file()]
    removed = _remove_files(
        files, mode, log, message_prefix="清理文件", exceed_limit=False
    )
    return removed


def _remove_files(
    paths: list[Path],
    mode: str,
    log: LogCallback,
    *,
    message_prefix: str,
    exceed_limit: bool,
) -> int:
    """按 ``mode`` 删除 ``paths``，失败则记录日志并继续。"""

    removed = 0
    for path in paths:
        try:
            remove_path(path, mode)
        except (OSError, TrashError) as exc:
            log(f"{message_prefix}失败，已跳过: {path}: {exc}")
            continue
        removed += 1
    if exceed_limit:
        log(f"文件数量达到上限，清理 {removed} 个旧文件（{mode}）")
    else:
        log(f"已清理 {removed} 个文件（{mode}）")
    return removed
