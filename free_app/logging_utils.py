from __future__ import annotations

import re
from datetime import datetime

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
