"""free_app 各模块共用的小工具函数。

只依赖标准库、不引入第三方依赖，任何模块都能安全从这里导入，
不会造成循环导入。
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int, str], None]
OcrCallback = Callable[[bytes], list[str]]
OcrBox = tuple[str, list[tuple[int, int]]]
OcrBoxCallback = Callable[[bytes], list[OcrBox]]


def noop_log(log_callback: LogCallback | None) -> LogCallback:
    """返回原回调，或一个接收并丢弃消息的空操作回调。"""

    return log_callback or (lambda _message: None)


def number_setting(
    settings: dict[str, Any],
    key: str,
    default: float,
    *,
    minimum: float | None = None,
) -> float:
    """读取数字类型的设置项，信任配置清洗的结果。

    ``load_settings`` 保证这些键持有有限的 ``int``/``float``；出现非数字或
    非有限值属于程序错误，直接抛出 ``TypeError``/``ValueError``，而不是
    静默强转。
    """

    value = settings.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"设置 {key} 必须是数字，实际为 {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"设置 {key} 必须是有限数字，实际为 {value!r}")
    if minimum is not None:
        return max(minimum, float(value))
    return float(value)


def clamp_coord(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """把点击坐标限制到 ``[0, width-1] x [0, height-1]`` 范围内。"""

    return max(0, min(width - 1, x)), max(0, min(height - 1, y))


def unique_existing_paths(values: Iterable[Any]) -> list[Path]:
    """按顺序收集存在的路径，跳过 ``None`` 与重复项。"""

    paths: list[Path] = []
    seen: set[Path] = set()
    for value in values:
        if value is None:
            continue
        path = Path(value)
        if path in seen or not path.exists():
            continue
        seen.add(path)
        paths.append(path)
    return paths


def write_json_file(path: Path, data: Any) -> None:
    """把 ``data`` 以缩进的 UTF-8 JSON 原子写入 ``path``。

    内容先写入同目录的临时文件，再用 ``os.replace`` 移动到位，因此写入
    中途崩溃也不会留下被截断的配置/任务文件。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(payload, encoding="utf-8")
    os.replace(temp_path, path)


def deep_copy(value: Any) -> Any:
    """通过 JSON 往返深拷贝 JSON 形态的数据（dict/list）。

    这是项目让嵌套动作与任务字典脱离源缓冲区的既定做法；用本函数统一收口，
    避免各处重复写 ``json.loads(json.dumps(...))``。
    """

    return json.loads(json.dumps(value))
