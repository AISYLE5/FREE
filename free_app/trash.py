from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


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
