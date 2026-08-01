"""Small shared helpers used across free_app modules.

Kept dependency-free (only the standard library) so every module can import
from here without creating import cycles.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int, str], None]
OcrCallback = Callable[[bytes], list[str]]
OcrBox = tuple[str, list[tuple[int, int]]]
OcrBoxCallback = Callable[[bytes], list[OcrBox]]


def noop_log(log_callback: LogCallback | None) -> LogCallback:
    """Return the callback, or a no-op that accepts and discards messages."""

    return log_callback or (lambda _message: None)


def number_setting(
    settings: dict[str, Any],
    key: str,
    default: float,
    *,
    minimum: float | None = None,
) -> float:
    """Return a numeric setting with type-error fallback and optional floor."""

    try:
        value = float(settings.get(key, default))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def clamp_coord(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """Clamp a tap coordinate into ``[0, width-1] x [0, height-1]``."""

    return max(0, min(width - 1, x)), max(0, min(height - 1, y))


def string_list(value: Any) -> list[str]:
    """Normalize a string or list of strings into stripped, non-empty items."""

    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def unique_existing_paths(values: Iterable[Any]) -> list[Path]:
    """Collect existing paths in order, skipping ``None`` and duplicates."""

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


def deep_copy(value: Any) -> Any:
    """Deep-copy JSON-like data (dicts/lists) via a JSON round-trip.

    This is the project's established idiom for detaching nested action and
    task dictionaries from their source buffers; the helper keeps it in one
    place instead of repeating ``json.loads(json.dumps(...))`` everywhere.
    """

    return json.loads(json.dumps(value))
