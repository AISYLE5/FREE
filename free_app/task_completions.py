"""任务「今日完成」记录。

记录每个任务最近一次成功完成的**时刻**（本地时间），供两处消费：

- 设置页「今日完成」分页展示当天各项任务的完成情况，并可逐个 / 整体重置；
- 「执行全部」在开关打开时跳过当天已完成的任务（只跑今日未完成的项）。

判「今日」用日期比较而不是时间戳：跨天自动视为未完成，不需要定时清理，
也不会因为程序整夜没关而把昨天的记录算成今天。

存储位置与设置文件同目录（``config/task_completions.json``），内容是
``{任务 id: "YYYY-MM-DD HH:MM:SS"}``。这是一份可随时重建的状态文件，
损坏时按「没有记录」处理，不拦住程序启动。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .helpers import write_json_file

COMPLETIONS_FILE_NAME = "task_completions.json"

# 记录格式：本地时间、秒级精度，人类可读且便于手工订正。
COMPLETION_FORMAT = "%Y-%m-%d %H:%M:%S"


def completions_path(settings_path: Path) -> Path:
    """返回与 ``settings.json`` 同目录的完成记录文件路径。"""

    return settings_path.with_name(COMPLETIONS_FILE_NAME)


def format_completion(moment: datetime) -> str:
    """把完成时刻格式化为存储用的字符串。"""

    return moment.strftime(COMPLETION_FORMAT)


def parse_completion(value: Any) -> datetime | None:
    """把存储值解析为完成时刻；非字符串或无法解析时返回 ``None``。"""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), COMPLETION_FORMAT)
    except ValueError:
        return None


def sanitize_completions(data: Any) -> dict[str, str]:
    """把任意输入清洗为 ``{任务 id: 规范化完成时刻}``。

    非对象输入直接丢弃；键必须是去空白后仍非空的字符串；值必须能按
    :data:`COMPLETION_FORMAT` 解析，否则该条记录被剔除。
    """

    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, str] = {}
    for task_id, value in data.items():
        if not isinstance(task_id, str) or not task_id.strip():
            continue
        moment = parse_completion(value)
        if moment is None:
            continue
        cleaned[task_id.strip()] = format_completion(moment)
    return cleaned


def load_completions(path: Path) -> dict[str, str]:
    """读取完成记录；文件缺失、损坏或不是对象时返回空表。"""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return sanitize_completions(data)


def save_completions(path: Path, completions: Mapping[str, str]) -> None:
    """原子写回完成记录（按键排序，让文件 diff 稳定）。"""

    write_json_file(path, dict(sorted(completions.items())))


def mark_completed(
    path: Path,
    task_id: str,
    moment: datetime | None = None,
) -> dict[str, str]:
    """记录 ``task_id`` 的完成时刻，返回写入后的完整记录。

    以磁盘内容为基准合并，避免覆盖另一个页面刚写入的键。
    """

    completions = load_completions(path)
    completions[task_id] = format_completion(moment or datetime.now())
    save_completions(path, completions)
    return completions


def reset_completion(path: Path, task_id: str) -> dict[str, str]:
    """清除 ``task_id`` 的完成记录（重置为未完成），返回写入后的完整记录。"""

    completions = load_completions(path)
    if completions.pop(task_id, None) is None:
        return completions
    save_completions(path, completions)
    return completions


def reset_completions(path: Path) -> dict[str, str]:
    """清除全部完成记录，返回空表。"""

    save_completions(path, {})
    return {}


def completion_moment(completions: Mapping[str, str], task_id: str) -> datetime | None:
    """返回 ``task_id`` 的完成时刻；没有记录或记录非法时返回 ``None``。"""

    return parse_completion(completions.get(task_id))


def is_completed_on(
    completions: Mapping[str, str],
    task_id: str,
    day: date | None = None,
) -> bool:
    """``task_id`` 是否在 ``day``（默认今天）当天完成过。"""

    moment = completion_moment(completions, task_id)
    if moment is None:
        return False
    return moment.date() == (day or date.today())


def completed_task_ids(
    completions: Mapping[str, str],
    task_ids: Iterable[str],
    day: date | None = None,
) -> list[str]:
    """返回 ``task_ids`` 中在 ``day``（默认今天）当天已完成的任务 id。

    保持入参顺序，重复 id 只出现一次。
    """

    target = day or date.today()
    completed: list[str] = []
    for task_id in task_ids:
        if task_id in completed:
            continue
        if is_completed_on(completions, task_id, target):
            completed.append(task_id)
    return completed
