from __future__ import annotations

from pathlib import Path

from .helpers import LogCallback, noop_log
from .trash import TrashError, remove_path


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
