from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .action_schema import ACTIONS_DIRECTORY_NAME, COMPOUND_TYPE, validate_action_params
from .constants import MAX_OUTPUT_FILE_LIMIT, MAX_TASK_EXECUTION_COUNT
from .helpers import write_json_file
from .models import Action, TaskDefinition

SMTP_SECURITY_LEVELS = {"ssl", "starttls"}
OCR_DOWNLOAD_SOURCES = ("auto", "baidu", "modelscope", "huggingface")
# 设置默认值（settings.example.json 与设置页共用同一来源）。
DEFAULT_SMTP_HOST = "smtp.qq.com"
DEFAULT_SMTP_PORT = 465
DEFAULT_SMTP_SECURITY = "ssl"
DEFAULT_SMTP_TIMEOUT_SECONDS = 20
DEFAULT_SUBJECT_PREFIX = "FREE"
DEFAULT_NOTIFY_ON = ["success", "failed", "stopped"]
DEFAULT_CLEANUP_MODE = "recycle"
DEFAULT_MAX_LOG_FILES = -1
DEFAULT_MAX_SCREENSHOT_FILES = -1
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
    "task_order",
    "qq_group_name",
    "mumu_directory",
    "command_timeout_seconds",
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
    "log_foreground_package",
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


def _native_number(value: Any, default: float, *, integer: bool = False) -> int | float:
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
    """缺失设置文件时，用随附的示例配置创建用户的设置文件。

    成功新建时返回 ``True``；已存在的用户配置绝不覆盖。
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
    """原子写入设置文件，崩溃也不会留下被截断的文件。"""
    write_json_file(path, settings)


def update_settings(path: Path, updates: dict[str, Any]) -> dict[str, Any]:
    """把 ``updates`` 合并进磁盘上的设置文件（读取-修改-写入）。

    以磁盘内容为基准合并，避免覆盖其他页面刚写入的键；写入前做一次完整
    清洗，返回清洗后的完整设置，可直接作为调用方内存中的新快照。
    """

    persisted = load_json(path) if path.exists() else {}
    if not isinstance(persisted, dict):
        raise ValueError(f"配置文件必须是对象: {path}")
    persisted.update(updates)
    sanitized = _sanitize_settings(persisted)
    save_settings(path, sanitized)
    return sanitized


@dataclass(frozen=True)
class TaskFileError:
    """无法加载而被跳过的任务文件。"""

    path: Path
    reason: str


def load_task_directory(
    directory: Path,
    variables: dict[str, Any] | None = None,
    actions_directory: Path | None = None,
) -> tuple[list[TaskDefinition], list[TaskFileError]]:
    """加载 ``directory`` 下所有顶层的 ``*.json`` 任务文件。

    损坏的文件被跳过并报告为 ``TaskFileError``，其余任务继续加载。目录本身
    必须存在，且至少包含一个可用任务，否则抛出异常。

    ``actions_directory``（默认 ``config/actions``）中的复合动作会在加载时
    展开为原语动作。
    """

    tasks, errors, _raw = load_task_directory_raw(
        directory, variables, actions_directory
    )
    return tasks, errors


def load_task_directory_raw(
    directory: Path,
    variables: dict[str, Any] | None = None,
    actions_directory: Path | None = None,
) -> tuple[list[TaskDefinition], list[TaskFileError], dict[str, dict[str, Any]]]:
    """同 :func:`load_task_directory`，并额外返回校验通过任务的原始 JSON。

    返回的 ``raw_by_id`` 以任务 id 为键、保存变量替换前的文件原文，供任务
    管理页做编辑数据源，避免编辑器把任务文件读第二遍。
    """

    if not directory.exists() or not directory.is_dir():
        raise FileNotFoundError(f"任务目录不存在: {directory}")
    files = sorted(directory.glob("*.json"))
    if not files:
        raise ValueError(f"任务目录中没有任务文件: {directory}")
    variables = variables or {}
    actions_path = actions_directory or directory.parent / ACTIONS_DIRECTORY_NAME
    library = load_action_library(actions_path)

    parsed: list[tuple[Path, TaskDefinition | None, str | None, dict[str, Any]]] = []
    for path in files:
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            parsed.append((path, None, str(exc), {}))
            continue
        if not isinstance(data, dict):
            parsed.append((path, None, "任务文件必须是对象", {}))
            continue
        raw = data
        data = _substitute(data, variables)
        try:
            task = TaskDefinition.from_dict(data)
        except ValueError as exc:
            parsed.append((path, None, str(exc), {}))
            continue
        if not task.actions:
            parsed.append((path, None, "任务 actions 不能为空", {}))
            continue
        expanded_actions: list[Action] = []
        expansion_error: str | None = None
        for action in task.actions:
            sub_actions, expansion_error = _expand_action(
                action, library, variables, ()
            )
            if expansion_error is not None:
                break
            expanded_actions.extend(sub_actions)
        if expansion_error is not None:
            parsed.append((path, None, expansion_error, {}))
            continue
        task = replace(task, actions=tuple(expanded_actions))
        parsed.append((path, task, None, raw))

    id_counts: dict[str, int] = {}
    for _path, parsed_task, _reason, _raw in parsed:
        if parsed_task is not None:
            id_counts[parsed_task.id] = id_counts.get(parsed_task.id, 0) + 1

    tasks: list[TaskDefinition] = []
    errors: list[TaskFileError] = []
    raw_by_id: dict[str, dict[str, Any]] = {}
    for path, parsed_task, reason, raw in parsed:
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
            raw_by_id[parsed_task.id] = raw
    if not tasks:
        details = "；".join(f"{error.path.name}: {error.reason}" for error in errors)
        raise ValueError(f"任务目录中没有可用任务: {details}")
    return tasks, errors, raw_by_id


def load_action_library(directory: Path) -> dict[str, dict[str, Any]]:
    """从 ``directory`` 加载复合动作定义。

    返回“复合动作名 -> 原始定义 dict”的映射（含 ``steps`` 及其他自定义
    字段），任务加载器与任务管理页编辑器共用同一份内存形态。损坏的文件
    仍以文件名（去扩展名）为键记录，让引用它的任务能报告具体原因，而不是
    笼统的“不存在”。
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
        library[name.strip()] = data
    return library


def _expand_action(
    action: Action,
    library: dict[str, dict[str, Any]],
    variables: dict[str, Any],
    stack: tuple[str, ...],
) -> tuple[list[Action], str | None]:
    """把一个动作展开为原语动作。

    复合动作引用递归解析，带循环引用检测与 ``${param}`` 替换。
    返回 ``(actions, error)``。
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
    merged = dict(variables)
    outer_retries = action.parameters.get("retries")
    expanded: list[Action] = []
    for step in definition["steps"]:
        if not isinstance(step, dict):
            return [], f"复合动作 {name} 的步骤必须是对象"
        step_data = _substitute(step, merged)
        try:
            step_action = Action.from_dict(step_data)
        except ValueError as exc:
            return [], f"复合动作 {name} 步骤无效: {exc}"
        if outer_retries is not None:
            step_params = dict(step_action.parameters)
            step_params.setdefault("retries", outer_retries)
            step_action = replace(step_action, parameters=step_params)
        sub_actions, error = _expand_action(
            step_action, library, merged, (*stack, name)
        )
        if error is not None:
            return [], error
        expanded.extend(sub_actions)
    return expanded, None


def expand_action_for_run(
    action: dict[str, Any],
    library: dict[str, dict[str, Any]],
    variables: dict[str, Any] | None = None,
) -> tuple[list[Action], str | None]:
    """为单次运行解析占位符并递归展开一个动作。

    返回可直接交给引擎的原语动作，或一条错误信息。
    """

    variables = variables or {}
    try:
        # 不做标量强转：_substitute 对整串 ${var} 占位符已保留变量原始类型，
        # 其余字符串（含字面量 "123"/"true"）原样保留，避免误伤文本参数。
        substituted = _substitute(action, variables)
        parsed = Action.from_dict(substituted)
    except ValueError as exc:
        return [], str(exc)
    return _expand_action(parsed, library, variables, ())


def order_tasks(tasks: list[TaskDefinition], task_order: Any) -> list[TaskDefinition]:
    """按已保存的顺序排列任务，新增任务追加到末尾。"""

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
    """清洗项目配置文件中的各项取值。"""

    sanitized: dict[str, Any] = {
        key: value for key, value in settings.items() if key in NATIVE_SETTING_KEYS
    }
    string_defaults = {
        "log_directory": "logs",
        "screenshot_directory": "screenshots",
        "ocr_model_directory": "models",
        "cleanup_mode": DEFAULT_CLEANUP_MODE,
    }
    for key, fallback in string_defaults.items():
        value = sanitized.get(key)
        if not isinstance(value, str) or not value.strip():
            sanitized[key] = fallback

    source = sanitized.get("ocr_download_source")
    sanitized["ocr_download_source"] = (
        source if source in OCR_DOWNLOAD_SOURCES else "auto"
    )

    for key in (
        "adb_path",
        "mumu_cli_path",
        "qq_group_name",
    ):
        if not isinstance(sanitized.get(key), str):
            sanitized[key] = ""

    bool_defaults = {
        "auto_start_mumu": True,
        "close_mumu_after_run": False,
        "close_mumu_app_after_run": False,
        "log_foreground_package": False,
    }
    for key, flag_default in bool_defaults.items():
        sanitized[key] = _native_bool(sanitized.get(key), flag_default)

    # 任务结束后是否清理 App 及等待时长尊重配置文件（settings.example.json
    # 默认 true / 5），app_lifecycle 的 False 分支按此生效。
    sanitized["cleanup_after_task"] = _native_bool(
        sanitized.get("cleanup_after_task"), True
    )
    sanitized["cleanup_delay_seconds"] = max(
        0,
        int(_native_number(sanitized.get("cleanup_delay_seconds"), 5, integer=True)),
    )

    # 未配置 MuMu 安装目录时从 adb_path 推导（nx_main/shell 形态取上级目录）。
    mumu_directory = sanitized.get("mumu_directory")
    if not isinstance(mumu_directory, str) or not mumu_directory.strip():
        sanitized["mumu_directory"] = _derive_mumu_directory(sanitized)

    # MuMu 启动/轮询/命令超时与 ADB 命令超时固定写死，界面不提供入口，也不随配置文件变化。
    sanitized["mumu_start_timeout_seconds"] = 30
    sanitized["mumu_poll_interval_seconds"] = 3
    sanitized["mumu_command_timeout_seconds"] = 10
    sanitized["command_timeout_seconds"] = 10

    numeric_defaults = {
        "mumu_vm_index": (0, True),
        "max_log_files": (DEFAULT_MAX_LOG_FILES, True),
        "max_screenshot_files": (DEFAULT_MAX_SCREENSHOT_FILES, True),
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

    # 是否保存截图由 max_screenshot_files 控制：0 = 不保存，负数 = 不限制，正数 = 保留最新 N 个。
    sanitized["max_log_files"] = min(
        MAX_OUTPUT_FILE_LIMIT, max(-1, sanitized["max_log_files"])
    )
    sanitized["max_screenshot_files"] = min(
        MAX_OUTPUT_FILE_LIMIT, max(-1, sanitized["max_screenshot_files"])
    )

    cleanup_mode = sanitized.get("cleanup_mode")
    if cleanup_mode not in {"recycle", "permanent"}:
        sanitized["cleanup_mode"] = "recycle"

    sanitized["email_notification"] = _sanitize_email(sanitized)
    return sanitized


def _sanitize_email(settings: dict[str, Any]) -> dict[str, Any]:
    """清洗设置文件中的 ``email_notification`` 配置块。"""

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
    email["smtp_port"] = max(
        1,
        min(
            65535,
            int(
                _native_number(email.get("smtp_port"), DEFAULT_SMTP_PORT, integer=True)
            ),
        ),
    )
    security = email.get("smtp_security")
    security = (
        security.strip().lower()
        if isinstance(security, str)
        and security.strip().lower() in SMTP_SECURITY_LEVELS
        else DEFAULT_SMTP_SECURITY
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
        1,
        int(
            _native_number(
                email.get("smtp_timeout_seconds"),
                DEFAULT_SMTP_TIMEOUT_SECONDS,
                integer=True,
            )
        ),
    )
    return email


def _derive_mumu_directory(settings: dict[str, Any]) -> str:
    """从配置的 adb.exe 路径推导 MuMu 安装目录。"""

    adb_value = settings.get("adb_path")
    if isinstance(adb_value, str) and adb_value.strip():
        path = Path(adb_value.strip())
        if path.name.lower() == "adb.exe" and path.parent.name.lower() in {
            "nx_main",
            "shell",
        }:
            return str(path.parent.parent)
        return str(path.parent)
    return ""


def _placeholder_key(stripped: str) -> str | None:
    """整串恰好是一个 ${key} 占位符时返回其中的 key，否则返回 ``None``。"""

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
        replacement = variables.get(key) if key is not None else None
        if key is not None and replacement is not None:
            # 整串占位符：直接返回变量的原始类型（数字/布尔原样保留），
            # 避免后续类型强转把原本就是 "123"/"true" 的文本参数误伤。
            if isinstance(replacement, list):
                return [_substitute(part, variables) for part in replacement]
            return replacement
        replaced = value
        for key, replacement in variables.items():
            if replacement is None:
                # None 不是可插入的文本；保留原占位符，避免注入字面量 "None"。
                continue
            if isinstance(replacement, list):
                text = ", ".join(str(item) for item in replacement)
            elif isinstance(replacement, bool):
                text = "true" if replacement else "false"
            else:
                text = str(replacement)
            replaced = replaced.replace("${" + key + "}", text)
        return replaced
    return value
