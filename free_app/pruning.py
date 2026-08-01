from __future__ import annotations

from pathlib import Path

from .helpers import LogCallback, noop_log
from .trash import TrashError, send_to_recycle_bin


def prune_files(
    directory: Path,
    max_files: int,
    mode: str,
    log_callback: LogCallback | None = None,
) -> int:
    """Keep the newest max_files files and remove older ones per cleanup mode."""

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
    removed = _remove_files(to_remove, mode, log, message_prefix="清理旧文件", exceed_limit=True)
    return removed


def clear_output_files(
    directory: Path,
    mode: str,
    log_callback: LogCallback | None = None,
) -> int:
    """Delete every file below ``directory`` using the configured cleanup mode.

    ``recycle`` sends each file to the Windows Recycle Bin; ``permanent``
    removes it immediately.  This is the manual “清理全部日志/截图” action.
    """

    log = noop_log(log_callback)
    if not directory.is_dir():
        return 0
    files = [path for path in directory.rglob("*") if path.is_file()]
    removed = _remove_files(files, mode, log, message_prefix="清理文件", exceed_limit=False)
    return removed


def _remove_files(
    paths: list[Path],
    mode: str,
    log: LogCallback,
    *,
    message_prefix: str,
    exceed_limit: bool,
) -> int:
    """Remove ``paths`` per ``mode``, logging failures and continuing."""

    removed = 0
    for path in paths:
        try:
            if mode == "permanent":
                path.unlink()
            else:
                send_to_recycle_bin(path)
        except (OSError, TrashError) as exc:
            log(f"{message_prefix}失败，已跳过: {path}: {exc}")
            continue
        removed += 1
    if exceed_limit:
        log(f"文件数量达到上限，清理 {removed} 个旧文件（{mode}）")
    else:
        log(f"已清理 {removed} 个文件（{mode}）")
    return removed
