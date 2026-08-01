from __future__ import annotations

import re
from datetime import datetime

SUMMARY_MARKERS = (
    "开始执行",
    "开始任务",
    "运行标识",
    "运行结束",
    "任务结果",
    "任务尝试",
    "任务完成",
    "任务失败",
    "任务异常",
    "任务已停止",
    "已停止",
    "停止信号",
    "全部任务执行",
    "准备连接设备",
    "准备 MuMu",
    "设备准备失败",
    "批量任务准备失败",
    "ADB 命令失败",
    "MuMu 命令失败",
    "动作失败",
    "动作最终失败",
    "重试前",
    "App 清理失败",
    "关闭 App 进程失败",
    "已连接 MuMu",
    "设置已保存",
    "邮件通知",
    "关键页面截图",
    "失败截图",
    "关闭 MuMu",
    "已关闭 MuMu",
    "开始关闭 App 进程: ",
)

_TIMESTAMP_PREFIX = re.compile(r"^\[\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\]\s")


def format_log_line(message: str, *, now: datetime | None = None) -> str:
    """Return one normalized, timestamped log line.

    Engine messages already carry a timestamp, while messages emitted by the
    workers and housekeeping helpers historically did not.  Keeping
    the normalization at the output boundary makes the GUI logs
    equally useful without forcing every caller to know about formatting.
    """

    line = str(message).replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\r")
    if _TIMESTAMP_PREFIX.match(line):
        return line
    timestamp = now or datetime.now()
    return f"[{timestamp:%H:%M:%S}] {line}"


def should_write_log_line(level: str, message: str) -> bool:
    """Decide whether a run log line belongs in the saved log file."""

    if level == "all":
        return True
    if level == "none":
        # "none" can only come from a stale settings file; never write logs for it.
        return False
    return any(marker in message for marker in SUMMARY_MARKERS)
