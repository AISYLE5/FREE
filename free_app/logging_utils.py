from __future__ import annotations

import re
from datetime import datetime

_TIMESTAMP_PREFIX = re.compile(r"^\[\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\]\s")


def format_log_line(message: str, *, now: datetime | None = None) -> str:
    """返回一条规范化、带时间戳的日志行。

    引擎消息本身已带时间戳，而 worker 与收尾辅助函数发出的消息
    历史上没有。把规范化保留在输出边界，GUI 日志即可同样有用，
    又不必强迫每个调用方关心格式化。
    """

    line = str(message).replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\r")
    if _TIMESTAMP_PREFIX.match(line):
        return line
    timestamp = now or datetime.now()
    return f"[{timestamp:%H:%M:%S}] {line}"
