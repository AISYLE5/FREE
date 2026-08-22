from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# 复合动作库目录名（相对 config/）
ACTIONS_DIRECTORY_NAME = "actions"

# 原语动作类型（引擎直接执行的最小动作）
PRIMITIVE_TYPES = (
    "launch",
    "stop",
    "wait",
    "back",
    "click",
    "swipe",
    "detect",
    "if",
    "loop_until",
    "capture_screenshot",
)
PRIMITIVE_TYPE_SET = set(PRIMITIVE_TYPES)

COMPOUND_TYPE = "compound"

TEXT_MATCH_MODES = ("exact", "fuzzy")
DETECT_LOCATES = ("ocr", "ui")
DETECT_TARGETS = ("text", "resource_id")
CLICK_LOCATES = ("ui", "ocr", "coordinate")
CLICK_UI_TARGETS = ("text", "resource_id")


@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    kind: str = "text"  # text | number | bool | list | select
    required: bool = False
    default: Any = None
    options: tuple[str, ...] = ()
    placeholder: str = ""


RETRIES_SPEC = ParamSpec("retries", "重试次数", "number", default=0)


def _with_retries(specs: tuple[ParamSpec, ...]) -> tuple[ParamSpec, ...]:
    return (*specs, RETRIES_SPEC)


def _polling_specs(timeout: int, interval: float) -> tuple[ParamSpec, ...]:
    """Return the shared timeout/interval polling specs for click and detect."""
    return (
        ParamSpec("timeout_seconds", "超时(秒)", "number", default=timeout),
        ParamSpec("interval_seconds", "轮询间隔(秒)", "number", default=interval),
    )


_NUMBER_TYPES = (int, float)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value: Any) -> bool:
    return isinstance(value, _NUMBER_TYPES) and not isinstance(value, bool)


def _bool(value: Any) -> bool:
    return isinstance(value, bool)


def _list_of_text(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def _list_of_actions(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("type"), str)
        and bool(item.get("type", "").strip())
        for item in value
    )


# Required / optional per-kind validation dispatch tables. Each validator takes
# ``(item, spec)``; ``spec`` is only meaningful for the ``select`` kind.
_REQUIRED_VALIDATORS: dict[str, Callable[[Any, ParamSpec], bool]] = {
    "text": lambda item, spec: _text(item),
    "number": lambda item, spec: _number(item),
    "bool": lambda item, spec: _bool(item),
    "value": lambda item, spec: _text(item) or isinstance(item, bool),
    "list": lambda item, spec: _list_of_text(item) or _text(item),
    "actions": lambda item, spec: _list_of_actions(item),
    "select": lambda item, spec: _text(item) and str(item) in spec.options,
}

_OPTIONAL_VALIDATORS: dict[str, Callable[[Any, ParamSpec], bool]] = {
    "text": lambda item, spec: isinstance(item, str),
    "number": lambda item, spec: _number(item),
    "bool": lambda item, spec: _bool(item),
    "value": lambda item, spec: isinstance(item, (str, bool)),
    "list": lambda item, spec: isinstance(item, (list, str)),
    "actions": lambda item, spec: isinstance(item, list),
    "select": lambda item, spec: isinstance(item, str) and item in spec.options,
}


def _validate_nested_steps(steps: Any, label: str, errors: list[str]) -> None:
    if not isinstance(steps, list):
        return
    for index, step in enumerate(steps):
        prefix = f"{label}[{index}]"
        if not isinstance(step, dict) or not _text(step.get("type")):
            errors.append(f"{prefix} 必须是带 type 的动作对象")
            continue
        step_type = str(step["type"]).strip()
        if step_type not in PRIMITIVE_TYPE_SET:
            errors.append(f"{prefix} 未知动作类型: {step_type}")
            continue
        step_params = {key: value for key, value in step.items() if key != "type"}
        nested_errors = validate_action_params(step_type, step_params)
        errors.extend(f"{prefix}: {error}" for error in nested_errors)


CLICK_SPECS: dict[str, tuple[ParamSpec, ...]] = {
    "ui_text": _with_retries(
        (
            ParamSpec("text", "目标文本", "list", required=True, placeholder="例如：签到,领取"),
            ParamSpec("skip_if_texts", "跳过条件", "list", placeholder="例如：已签到,已领取"),
            ParamSpec("match_mode", "匹配方式", "select", default="exact", options=TEXT_MATCH_MODES),
            *_polling_specs(15, 0.5),
        )
    ),
    "ui_resource_id": _with_retries(
        (
            ParamSpec("resource_id", "Resource ID", "text", required=True),
            *_polling_specs(15, 0.5),
        )
    ),
    "ocr": _with_retries(
        (
            ParamSpec("text", "需要 OCR 确认的文本", "list", required=True, placeholder="例如：签到,领取"),
            ParamSpec("skip_if_texts", "跳过条件", "list", placeholder="例如：已签到,已领取"),
            ParamSpec("match_mode", "匹配方式", "select", default="exact", options=TEXT_MATCH_MODES),
            *_polling_specs(15, 0.5),
        )
    ),
    "coordinate": _with_retries(
        (
            ParamSpec("x", "X 坐标", "number", required=True),
            ParamSpec("y", "Y 坐标", "number", required=True),
        )
    ),
}

DETECT_SPECS: dict[str, tuple[ParamSpec, ...]] = {
    "ocr": _with_retries(
        (
            ParamSpec("texts", "目标文本", "list", required=True, placeholder="例如：签到,领取"),
            ParamSpec("result_var", "结果变量", "text", required=True, placeholder="例如：ocr_state"),
            ParamSpec("match_mode", "匹配方式", "select", default="exact", options=TEXT_MATCH_MODES),
            *_polling_specs(30, 1),
            ParamSpec("continue_on_timeout", "超时继续", "bool", default=False),
        )
    ),
    "ui_text": _with_retries(
        (
            ParamSpec("texts", "目标文本", "list", required=True, placeholder="例如：签到,领取"),
            ParamSpec("result_var", "结果变量", "text", required=True, placeholder="例如：ui_state"),
            ParamSpec("match_mode", "匹配方式", "select", default="exact", options=TEXT_MATCH_MODES),
            *_polling_specs(30, 1),
            ParamSpec("continue_on_timeout", "超时继续", "bool", default=False),
        )
    ),
    "ui_resource_id": _with_retries(
        (
            ParamSpec("resource_id", "Resource ID", "text", required=True),
            ParamSpec("result_var", "结果变量", "text", required=True, placeholder="例如：ui_state"),
            *_polling_specs(30, 1),
            ParamSpec("continue_on_timeout", "超时继续", "bool", default=False),
        )
    ),
}

IF_SPECS = _with_retries(
    (
        ParamSpec("var", "条件变量", "text", required=True, placeholder="例如：ocr_state"),
        ParamSpec("equals", "等于", "value", required=True, placeholder="例如：领取 或 true"),
        ParamSpec("then", "成立时步骤", "actions"),
        ParamSpec("else", "不成立时步骤", "actions"),
    )
)

LOOP_UNTIL_SPECS = _with_retries(
    (
        ParamSpec("var", "条件变量", "text", required=True, placeholder="例如：state"),
        ParamSpec("equals", "等于", "value", required=True, placeholder="例如：true"),
        ParamSpec("max_iterations", "最大次数", "number", default=1),
        ParamSpec("steps", "循环步骤", "actions"),
    )
)

LIFECYCLE_SPECS: dict[str, tuple[ParamSpec, ...]] = {
    "stop": _with_retries(
        (ParamSpec("package", "目标应用包名", "text", placeholder="留空使用任务包名"),)
    ),
    "launch": _with_retries(
        (
            ParamSpec("package", "目标应用包名", "text", placeholder="留空使用任务包名"),
            ParamSpec("wait_seconds", "启动后等待(秒)", "number", placeholder="3"),
            ParamSpec("launch_attempts", "启动尝试次数", "number", default=3),
        )
    ),
    "wait": _with_retries(
        (ParamSpec("seconds", "等待秒数", "number", default=1),)
    ),
    "back": _with_retries(()),
}

SWIPE_SPECS = _with_retries(
    (
        ParamSpec("x1", "起点 X", "number", required=True),
        ParamSpec("y1", "起点 Y", "number", required=True),
        ParamSpec("x2", "终点 X", "number", required=True),
        ParamSpec("y2", "终点 Y", "number", required=True),
        ParamSpec("duration_ms", "时长(毫秒)", "number", default=300),
    )
)

SCREENSHOT_SPECS = _with_retries(
    ()
)


# Static action type -> spec lookup. Click/detect are resolved dynamically
# because their spec depends on locate/target params; everything else is fixed.
_TYPE_SPECS: dict[str, tuple[ParamSpec, ...]] = {
    "swipe": SWIPE_SPECS,
    "if": IF_SPECS,
    "loop_until": LOOP_UNTIL_SPECS,
    "capture_screenshot": SCREENSHOT_SPECS,
    **LIFECYCLE_SPECS,
}


def _specs_for(action_type: str, params: dict[str, Any]) -> tuple[ParamSpec, ...]:
    if action_type == "click":
        locate = str(params.get("locate", "ui"))
        if locate == "ui":
            target = str(params.get("target", "text"))
            return CLICK_SPECS.get(f"ui_{target}", CLICK_SPECS["ui_text"])
        return CLICK_SPECS.get(locate, CLICK_SPECS["ocr"])
    if action_type == "detect":
        locate = str(params.get("locate", "ocr"))
        if locate == "ui":
            target = str(params.get("target", "text"))
            return DETECT_SPECS.get(f"ui_{target}", DETECT_SPECS["ui_text"])
        return DETECT_SPECS.get(locate, DETECT_SPECS["ocr"])
    return _TYPE_SPECS.get(action_type, ())


def _allowed_parameter_keys(action_type: str) -> set[str]:
    """Return the native parameter keys accepted for a primitive action."""

    if action_type == "click":
        keys: set[str] = {"locate", "target"}
        for specs in CLICK_SPECS.values():
            keys.update(spec.key for spec in specs)
        return keys
    if action_type == "detect":
        keys = {"locate", "target"}
        for specs in DETECT_SPECS.values():
            keys.update(spec.key for spec in specs)
        return keys
    return {spec.key for spec in _specs_for(action_type, {})}


def resolve_locate_target(action_type: str, params: dict[str, Any]) -> tuple[str, str]:
    """Resolve the locate (and ui-target) defaults used by click/detect.

    Returns ``(locate, target)``. When ``locate`` resolves to ``"ui"`` the
    target defaults to ``"text"``; otherwise the target is left as a raw string
    (defaulting to ``""``) since it is meaningless for non-ui locations.
    """
    if action_type == "click":
        locate = str(params.get("locate", "ui"))
        target = (
            str(params.get("target", "text"))
            if locate == "ui"
            else (str(params.get("target", "")) or "")
        )
        return locate, target
    if action_type == "detect":
        locate = str(params.get("locate", "ocr"))
        target = (
            str(params.get("target", "text"))
            if locate == "ui"
            else (str(params.get("target", "")) or "")
        )
        return locate, target
    return "", ""


def _validate_locate_target(action_type: str, params: dict[str, Any], errors: list[str]) -> None:
    """Validate the click/detect locate/target combos (error text preserved verbatim)."""
    locate, target = resolve_locate_target(action_type, params)
    if action_type == "click":
        if not _text(locate) or locate not in CLICK_LOCATES:
            errors.append(f"click 的 locate 必须是 {'/'.join(CLICK_LOCATES)} 之一")
        if locate == "ui":
            if not _text(target) or target not in CLICK_UI_TARGETS:
                errors.append(f"click ui 的 target 必须是 {'/'.join(CLICK_UI_TARGETS)} 之一")
    elif action_type == "detect":
        if not _text(locate) or locate not in DETECT_LOCATES:
            errors.append(f"detect 的 locate 必须是 {'/'.join(DETECT_LOCATES)} 之一")
        if locate == "ui":
            if not _text(target) or target not in DETECT_TARGETS:
                errors.append(f"detect ui 的 target 必须是 {'/'.join(DETECT_TARGETS)} 之一")


def validate_action_params(action_type: str, params: dict[str, Any]) -> list[str]:
    """Return a list of validation errors for a primitive action (empty when valid)."""

    if action_type == "compound":
        errors = []
        name = params.get("name")
        if not _text(name):
            errors.append("复合动作缺少 name")
        retries = params.get("retries")
        if retries is not None and not _number(retries):
            errors.append("重试次数（retries）格式不正确")
        unknown = [key for key in params if key not in {"name", "retries", "description"}]
        if unknown:
            errors.append(f"compound 不支持参数: {', '.join(str(key) for key in sorted(unknown))}")
        return errors
    if action_type not in PRIMITIVE_TYPE_SET:
        return [f"未知动作类型: {action_type}"]

    errors = []
    _validate_locate_target(action_type, params, errors)
    for spec in _specs_for(action_type, params):
        value = params.get(spec.key)
        if spec.required:
            valid = _REQUIRED_VALIDATORS[spec.kind](value, spec)
            if not valid:
                errors.append(f"{spec.label}（{spec.key}）为必填项")
            continue
        if value is None:
            continue
        valid = _OPTIONAL_VALIDATORS[spec.kind](value, spec)
        if not valid:
            errors.append(f"{spec.label}（{spec.key}）格式不正确")
    if action_type == "if":
        for branch_key in ("then", "else"):
            _validate_nested_steps(params.get(branch_key), f"if {branch_key}", errors)
    elif action_type == "loop_until":
        _validate_nested_steps(params.get("steps"), f"{action_type} steps", errors)
    allowed = _allowed_parameter_keys(action_type) | {"description"}
    unknown = [key for key in params if key not in allowed]
    if unknown:
        errors.append(f"{action_type} 不支持参数: {', '.join(str(key) for key in sorted(unknown))}")
    return errors


def effective_parameters(action_type: str, params: dict[str, Any]) -> dict[str, Any]:
    """Fill documented defaults into parameters without overriding user values."""

    effective = dict(params)
    if action_type in ("click", "detect"):
        locate, target = resolve_locate_target(action_type, effective)
        effective.setdefault("locate", locate)
        if effective.get("locate") == "ui":
            effective.setdefault("target", target)
    for spec in _specs_for(action_type, effective):
        if spec.default is not None and effective.get(spec.key) is None:
            effective[spec.key] = spec.default
    return effective


def _join_text_values(value: Any) -> str:
    """Render a text target (single string or list) for human-readable text."""

    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value) or "-"
    text = str(value).strip()
    return text or "-"


def describe_action(action_type: str, params: dict[str, Any]) -> str:
    """Short human-readable description used by logs and the editor."""

    effective = effective_parameters(action_type, params)
    if action_type == "click":
        locate = str(effective.get("locate", "ui"))
        if locate == "ui":
            target = str(effective.get("target", "text"))
            if target == "text":
                suffix = "（模糊）" if effective.get("match_mode") == "fuzzy" else ""
                return f"点击文本: {_join_text_values(effective.get('text', ''))}{suffix}"
            return f"点击控件: {effective.get('resource_id', '')}"
        if locate == "ocr":
            suffix = "（模糊）" if effective.get("match_mode") == "fuzzy" else ""
            return f"OCR 点击文本: {_join_text_values(effective.get('text', ''))}{suffix}"
        return f"点击坐标: ({effective.get('x', '')}, {effective.get('y', '')})"
    if action_type == "swipe":
        return "滑动"
    if action_type == "detect":
        locate = str(effective.get("locate", "ocr"))
        result_var = str(effective.get("result_var", ""))
        if locate == "ui":
            target = str(effective.get("target", "text"))
            if target == "resource_id":
                return f"UI 检测控件: {effective.get('resource_id', '')} -> {result_var}"
            suffix = "（模糊）" if effective.get("match_mode") == "fuzzy" else ""
            return f"UI 检测文本: {_join_text_values(effective.get('texts', ''))}{suffix} -> {result_var}"
        suffix = "（模糊）" if effective.get("match_mode") == "fuzzy" else ""
        return f"OCR 检测文本: {_join_text_values(effective.get('texts', ''))}{suffix} -> {result_var}"
    if action_type == "if":
        equals = effective.get("equals")
        if isinstance(equals, bool):
            return f"分支: {effective.get('var', '')} == {str(equals).lower()}"
        return f"分支: {effective.get('var', '')} == {effective.get('equals', '')}"
    if action_type == "loop_until":
        return f"循环直到 {effective.get('var', '')} == {effective.get('equals', '')}"
    if action_type == "stop":
        return "退出应用"
    if action_type == "launch":
        return "启动应用"
    if action_type == "wait":
        return f"等待 {effective.get('seconds', 1)} 秒"
    if action_type == "back":
        return "返回"
    if action_type == "capture_screenshot":
        return "截图"
    if action_type == "compound":
        return f"复合动作: {effective.get('name', '')}"
    return action_type
