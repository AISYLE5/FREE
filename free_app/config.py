from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .action_schema import ACTIONS_DIRECTORY_NAME, COMPOUND_TYPE, validate_action_params
from .constants import MAX_OUTPUT_FILE_LIMIT, MAX_TASK_EXECUTION_COUNT
from .models import Action, TaskDefinition

SMTP_SECURITY_LEVELS = {"ssl", "starttls"}
OCR_DOWNLOAD_SOURCES = ("auto", "baidu", "modelscope", "huggingface")
DEFAULT_SMTP_HOST = "smtp.qq.com"
DEFAULT_SUBJECT_PREFIX = "FREE"
DEFAULT_NOTIFY_ON = ["success", "failed", "stopped"]
NATIVE_SETTING_KEYS = {
    "adb_path",
    "mumu_cli_path",
    "mumu_vm_index",
    "auto_start_mumu",
    "close_mumu_after_run",
    "close_mumu_app_after_run",
    "mumu_start_timeout_seconds",
    "mumu_poll_interval_seconds",
    "mumu_command_timeout_seconds",
    "cleanup_after_task",
    "cleanup_delay_seconds",
    "cleanup_packages",
    "task_order",
    "qq_group_name",
    "mumu_directory",
    "command_timeout_seconds",
    "poll_interval_seconds",
    "log_directory",
    "screenshot_directory",
    "ocr_model_directory",
    "ocr_det_model",
    "ocr_rec_model",
    "email_notification",
    "max_log_files",
    "max_screenshot_files",
    "cleanup_mode",
    "task_execution_counts",
    "ocr_download_source",
}
NATIVE_EMAIL_KEYS = {
    "enabled",
    "smtp_host",
    "smtp_port",
    "smtp_security",
    "smtp_username",
    "smtp_password",
    "recipients",
    "subject_prefix",
    "notify_on",
    "smtp_timeout_seconds",
}


def _native_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _native_number(value: Any, default: int | float, *, integer: bool = False) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if not math.isfinite(value):
        return default
    if integer and isinstance(value, float) and not value.is_integer():
        return default
    return int(value) if integer else float(value)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    loaded = load_json(path)
    if not isinstance(loaded, dict):
        raise ValueError(f"配置文件必须是对象: {path}")

    return _sanitize_settings(dict(loaded))


def ensure_settings_file(path: Path, template_path: Path | None = None) -> bool:
    """Create the user's settings file from the shipped example if missing.

    Returns ``True`` when a new file was created and never overwrites an
    existing user configuration.
    """

    if path.exists():
        return False
    template = template_path or path.with_name("settings.example.json")
    if not template.exists():
        raise FileNotFoundError(
            f"配置文件不存在，且找不到默认配置模板: {path} / {template}"
        )
    loaded = load_json(template)
    if not isinstance(loaded, dict):
        raise ValueError(f"默认配置模板必须是对象: {template}")
    save_settings(path, loaded)
    return True


def save_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class TaskFileError:
    """A task file that could not be loaded and was skipped."""

    path: Path
    reason: str


def load_task_directory(
    directory: Path,
    variables: dict[str, Any] | None = None,
    actions_directory: Path | None = None,
) -> tuple[list[TaskDefinition], list[TaskFileError]]:
    """Load every top-level ``*.json`` task file under ``directory``.

    Broken files are skipped and reported as ``TaskFileError`` entries while
    valid tasks keep loading.  The directory itself must exist and contain at
    least one usable task, otherwise an exception is raised.

    Compound actions from ``actions_directory`` (default: ``config/actions``)
    are expanded into primitive actions at load time.
    """

    if not directory.exists() or not directory.is_dir():
        raise FileNotFoundError(f"任务目录不存在: {directory}")
    files = sorted(directory.glob("*.json"))
    if not files:
        raise ValueError(f"任务目录中没有任务文件: {directory}")
    variables = variables or {}
    actions_path = actions_directory or directory.parent / ACTIONS_DIRECTORY_NAME
    library = load_action_library(actions_path)

    parsed: list[tuple[Path, TaskDefinition | None, str | None]] = []
    for path in files:
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            parsed.append((path, None, str(exc)))
            continue
        if not isinstance(data, dict):
            parsed.append((path, None, "任务文件必须是对象"))
            continue
        data = _substitute(data, variables)
        try:
            task = TaskDefinition.from_dict(data)
        except ValueError as exc:
            parsed.append((path, None, str(exc)))
            continue
        if not task.actions:
            parsed.append((path, None, "任务 actions 不能为空"))
            continue
        expanded_actions: list[Action] = []
        expansion_error: str | None = None
        for action in task.actions:
            sub_actions, expansion_error = _expand_action(action, library, variables, ())
            if expansion_error is not None:
                break
            expanded_actions.extend(sub_actions)
        if expansion_error is not None:
            parsed.append((path, None, expansion_error))
            continue
        task = replace(task, actions=tuple(expanded_actions))
        parsed.append((path, task, None))

    id_counts: dict[str, int] = {}
    for _path, parsed_task, _reason in parsed:
        if parsed_task is not None:
            id_counts[parsed_task.id] = id_counts.get(parsed_task.id, 0) + 1

    tasks: list[TaskDefinition] = []
    errors: list[TaskFileError] = []
    for path, parsed_task, reason in parsed:
        if parsed_task is None:
            errors.append(TaskFileError(path, reason or "任务文件无效"))
        elif id_counts[parsed_task.id] > 1:
            errors.append(TaskFileError(path, f"任务 id 重复: {parsed_task.id}"))
        elif path.stem != parsed_task.id:
            errors.append(
                TaskFileError(
                    path,
                    f"文件名必须与任务 id 一致: {path.name!r} != {parsed_task.id!r}",
                )
            )
        else:
            tasks.append(parsed_task)
    if not tasks:
        details = "；".join(f"{error.path.name}: {error.reason}" for error in errors)
        raise ValueError(f"任务目录中没有可用任务: {details}")
    return tasks, errors


def load_action_library(directory: Path) -> dict[str, dict[str, Any]]:
    """Load compound-action definitions from ``directory``.

    Returns a map of compound name -> definition.  Broken files are still
    keyed by their file stem so that referencing tasks can report the
    underlying cause instead of a generic "missing" error.
    """

    library: dict[str, dict[str, Any]] = {}
    if not directory.exists():
        return library
    for path in sorted(directory.glob("*.json")):
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            library[path.stem] = {"error": f"{path.name}: {exc}"}
            continue
        if not isinstance(data, dict):
            library[path.stem] = {"error": f"{path.name}: 复合动作文件必须是对象"}
            continue
        name = data.get("name")
        steps = data.get("steps")
        if not isinstance(name, str) or not name.strip():
            library[path.stem] = {"error": f"{path.name}: 缺少 name"}
            continue
        if not isinstance(steps, list) or not steps:
            library[path.stem] = {"error": f"{path.name}: steps 必须是非空列表"}
            continue
        params = data.get("params", [])
        if not isinstance(params, list) or not all(
            isinstance(item, str) and item.strip() for item in params
        ):
            library[path.stem] = {"error": f"{path.name}: params 必须是字符串列表"}
            continue
        library[name.strip()] = {
            "path": path,
            "name": name.strip(),
            "params": [str(item).strip() for item in params],
            "steps": steps,
        }
    return library


def _coerce_scalars(value: Any) -> Any:
    """Restore scalar types after ${} substitution (JSON numbers become strings)."""

    if isinstance(value, dict):
        return {key: _coerce_scalars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_coerce_scalars(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() == "true":
            return True
        if stripped.lower() == "false":
            return False
        if re.fullmatch(r"-?\d+", stripped):
            # The regex already constrains the character set, so int() cannot fail.
            return int(stripped)
        if re.fullmatch(r"-?\d+\.\d+", stripped):
            # The regex already constrains the character set, so float() cannot fail.
            return float(stripped)
    return value


def _expand_action(
    action: Action,
    library: dict[str, dict[str, Any]],
    variables: dict[str, Any],
    stack: tuple[str, ...],
) -> tuple[list[Action], str | None]:
    """Expand one action into primitives.

    Compound references are resolved recursively with cycle detection and
    ``${param}`` substitution.  Returns ``(actions, error)``.
    """

    if action.type != COMPOUND_TYPE:
        errors = validate_action_params(action.type, action.parameters)
        if errors:
            return [], "；".join(errors)
        return [action], None
    name = str(action.parameters.get("name", "")).strip()
    if not name:
        return [], "复合动作缺少 name"
    definition = library.get(name)
    if definition is None:
        return [], f"复合动作不存在: {name}"
    if "error" in definition:
        return [], f"复合动作 {name} 已损坏: {definition['error']}"
    if name in stack:
        return [], f"复合动作循环引用: {' -> '.join((*stack, name))}"
    invocation_params = action.parameters.get("params", {})
    if not isinstance(invocation_params, dict):
        return [], f"复合动作 {name} 的 params 必须是对象"
    merged = dict(variables)
    for key, value in invocation_params.items():
        if isinstance(key, str):
            merged[key] = value
    outer_retries = action.parameters.get("retries")
    expanded: list[Action] = []
    for step in definition["steps"]:
        if not isinstance(step, dict):
            return [], f"复合动作 {name} 的步骤必须是对象"
        step_data = _coerce_scalars(_substitute(step, merged))
        try:
            step_action = Action.from_dict(step_data)
        except ValueError as exc:
            return [], f"复合动作 {name} 步骤无效: {exc}"
        if outer_retries is not None:
            step_params = dict(step_action.parameters)
            step_params.setdefault("retries", outer_retries)
            step_action = replace(step_action, parameters=step_params)
        sub_actions, error = _expand_action(step_action, library, merged, (*stack, name))
        if error is not None:
            return [], error
        expanded.extend(sub_actions)
    return expanded, None


def expand_action_for_run(
    action: dict[str, Any],
    library: dict[str, dict[str, Any]],
    variables: dict[str, Any] | None = None,
) -> tuple[list[Action], str | None]:
    """Resolve placeholders and recursively expand one action for a single run.

    Returns primitive actions ready for the engine, or an error message.
    """

    variables = variables or {}
    try:
        substituted = _coerce_scalars(_substitute(action, variables))
        parsed = Action.from_dict(substituted)
    except ValueError as exc:
        return [], str(exc)
    return _expand_action(parsed, library, variables, ())


def order_tasks(tasks: list[TaskDefinition], task_order: Any) -> list[TaskDefinition]:
    """Apply a saved order while keeping newly added tasks discoverable."""

    by_id = {task.id: task for task in tasks}
    ordered: list[TaskDefinition] = []
    seen: set[str] = set()
    if isinstance(task_order, list):
        for task_id in task_order:
            if not isinstance(task_id, str) or task_id in seen:
                continue
            task = by_id.get(task_id)
            if task is not None:
                ordered.append(task)
                seen.add(task_id)
    ordered.extend(task for task in tasks if task.id not in seen)
    return ordered


def resolve_path(value: str | None, base_directory: Path) -> Path:
    if not value:
        return base_directory
    path = Path(value)
    return path if path.is_absolute() else base_directory / path


def _sanitize_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Normalize values from the project configuration file."""

    sanitized: dict[str, Any] = {
        key: value for key, value in settings.items() if key in NATIVE_SETTING_KEYS
    }
    string_defaults = {
        "log_directory": "logs",
        "screenshot_directory": "screenshots",
        "ocr_model_directory": "models",
        "cleanup_mode": "recycle",
    }
    for key, fallback in string_defaults.items():
        value = sanitized.get(key)
        if not isinstance(value, str) or not value.strip():
            sanitized[key] = fallback

    source = sanitized.get("ocr_download_source")
    sanitized["ocr_download_source"] = source if source in OCR_DOWNLOAD_SOURCES else "auto"

    for key in (
        "adb_path",
        "mumu_cli_path",
        "qq_group_name",
        "ocr_det_model",
        "ocr_rec_model",
    ):
        if not isinstance(sanitized.get(key), str):
            sanitized[key] = ""

    bool_defaults = {
        "auto_start_mumu": True,
        "cleanup_after_task": True,
        "close_mumu_after_run": False,
        "close_mumu_app_after_run": False,
    }
    for key, flag_default in bool_defaults.items():
        sanitized[key] = _native_bool(sanitized.get(key), flag_default)

    # 是否保存截图由 max_screenshot_files 控制：0 = 不保存，负数 = 不限制，正数 = 保留最新 N 个。
    mumu_directory = sanitized.get("mumu_directory")
    if not isinstance(mumu_directory, str) or not mumu_directory.strip():
        sanitized["mumu_directory"] = _derive_mumu_directory(sanitized)

    numeric_defaults = {
        "mumu_vm_index": (0, True),
        "mumu_start_timeout_seconds": (90, False),
        "mumu_poll_interval_seconds": (1, False),
        "mumu_command_timeout_seconds": (30, False),
        "cleanup_delay_seconds": (3, False),
        "command_timeout_seconds": (15, False),
        "poll_interval_seconds": (0.5, False),
        "max_log_files": (-1, True),
        "max_screenshot_files": (-1, True),
    }
    for key, (numeric_default, integer) in numeric_defaults.items():
        sanitized[key] = _native_number(
            sanitized.get(key), numeric_default, integer=integer
        )

    execution_counts = sanitized.get("task_execution_counts")
    if not isinstance(execution_counts, dict):
        execution_counts = {}
    normalized_execution_counts: dict[str, int] = {}
    for task_id, value in execution_counts.items():
        if not isinstance(task_id, str) or not task_id.strip():
            continue
        count = _native_number(value, -1, integer=True)
        if count < 0:
            continue
        normalized_execution_counts[task_id] = int(
            min(MAX_TASK_EXECUTION_COUNT, max(0, count))
        )
    sanitized["task_execution_counts"] = normalized_execution_counts

    sanitized["max_log_files"] = min(MAX_OUTPUT_FILE_LIMIT, max(-1, sanitized["max_log_files"]))
    sanitized["max_screenshot_files"] = min(
        MAX_OUTPUT_FILE_LIMIT, max(-1, sanitized["max_screenshot_files"])
    )

    cleanup_mode = sanitized.get("cleanup_mode")
    if cleanup_mode not in {"recycle", "permanent"}:
        sanitized["cleanup_mode"] = "recycle"

    sanitized["email_notification"] = _sanitize_email(sanitized)
    return sanitized


def _sanitize_email(settings: dict[str, Any]) -> dict[str, Any]:
    """Normalize the ``email_notification`` block of the settings file."""

    email = settings.get("email_notification")
    email = dict(email) if isinstance(email, dict) else {}
    email = {key: value for key, value in email.items() if key in NATIVE_EMAIL_KEYS}
    email["enabled"] = _native_bool(email.get("enabled"), False)

    smtp_host = email.get("smtp_host")
    email["smtp_host"] = (
        smtp_host.strip()
        if isinstance(smtp_host, str) and smtp_host.strip()
        else DEFAULT_SMTP_HOST
    )
    email["smtp_port"] = _native_number(email.get("smtp_port"), 465, integer=True)
    security = email.get("smtp_security")
    security = (
        security.strip().lower()
        if isinstance(security, str) and security.strip().lower() in SMTP_SECURITY_LEVELS
        else "ssl"
    )
    email["smtp_security"] = security
    username = email.get("smtp_username", "")
    email["smtp_username"] = username if isinstance(username, str) else ""
    password = email.get("smtp_password", "")
    email["smtp_password"] = password if isinstance(password, str) else ""
    recipients = email.get("recipients", [])
    email["recipients"] = (
        [item.strip() for item in recipients if isinstance(item, str) and item.strip()]
        if isinstance(recipients, list)
        else []
    )
    subject_prefix = email.get("subject_prefix", DEFAULT_SUBJECT_PREFIX)
    email["subject_prefix"] = (
        subject_prefix.strip()
        if isinstance(subject_prefix, str) and subject_prefix.strip()
        else DEFAULT_SUBJECT_PREFIX
    )
    notify_on = email.get("notify_on", DEFAULT_NOTIFY_ON)
    email["notify_on"] = (
        [item for item in notify_on if isinstance(item, str)]
        if isinstance(notify_on, list)
        else DEFAULT_NOTIFY_ON
    )
    email["smtp_timeout_seconds"] = max(
        1, int(_native_number(email.get("smtp_timeout_seconds"), 10, integer=True))
    )
    return email


def _derive_mumu_directory(settings: dict[str, Any]) -> str:
    """Derive the MuMu install folder from the configured adb.exe path."""

    adb_value = settings.get("adb_path")
    if isinstance(adb_value, str) and adb_value.strip():
        path = Path(adb_value.strip())
        if path.name.lower() == "adb.exe" and path.parent.name.lower() in {"nx_main", "shell"}:
            return str(path.parent.parent)
        return str(path.parent)
    return ""


def _placeholder_key(stripped: str) -> str | None:
    """Return the ${key} name for a fully-parenthesized placeholder, else ``None``."""

    if stripped.startswith("${") and stripped.endswith("}"):
        return stripped[2:-1]
    return None


def _substitute(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _substitute(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        result: list[Any] = []
        for item in value:
            stripped = item.strip() if isinstance(item, str) else ""
            key = _placeholder_key(stripped)
            replacement = variables.get(key) if key else None
            if isinstance(replacement, list):
                result.extend(_substitute(part, variables) for part in replacement)
            else:
                result.append(_substitute(item, variables))
        return result
    if isinstance(value, str):
        stripped = value.strip()
        key = _placeholder_key(stripped)
        replacement = variables.get(key) if key else None
        if isinstance(replacement, list):
            return [_substitute(part, variables) for part in replacement]
        replaced = value
        for key, replacement in variables.items():
            replaced = replaced.replace("${" + key + "}", str(replacement))
        return replaced
    return value
