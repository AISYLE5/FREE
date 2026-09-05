from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
    "swipe_until",
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
    kind: str = "text"  # 取值类型：文本 | 数字 | 布尔 | 列表 | 选项
    required: bool = False
    default: Any = None
    options: tuple[str, ...] = ()
    placeholder: str = ""
    minimum: float | None = None  # number 类型的最小值（含）


RETRIES_SPEC = ParamSpec("retries", "重试次数", "number", default=0, minimum=0)


def _with_retries(specs: tuple[ParamSpec, ...]) -> tuple[ParamSpec, ...]:
    return (*specs, RETRIES_SPEC)


def _polling_specs(timeout: int, interval: float) -> tuple[ParamSpec, ...]:
    """返回 click/detect/swipe_until 共用的 timeout/interval 轮询参数规格。"""
    return (
        ParamSpec("timeout_seconds", "超时(秒)", "number", default=timeout, minimum=0),
        ParamSpec(
            "interval_seconds", "轮询间隔(秒)", "number", default=interval, minimum=0
        ),
    )


_NUMBER_TYPES = (int, float)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value: Any, minimum: float | None = None) -> bool:
    """数字（bool 不算）、必须有限，且可选地不低于 ``minimum``。

    非有限的 ``inf``/``nan`` 在此被拒绝，保证它们不会进入引擎的
    deadline/休眠计算（与 ``helpers.number_setting`` 一致）。
    """

    if isinstance(value, bool) or not isinstance(value, _NUMBER_TYPES):
        return False
    if not math.isfinite(value):
        return False
    return minimum is None or value >= minimum


def _bool(value: Any) -> bool:
    return isinstance(value, bool)


def _list_of_text(value: Any, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def _list_of_actions(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("type"), str)
        and bool(item.get("type", "").strip())
        for item in value
    )


# 各类型参数的必填/可选校验分发表。每个校验器接收
# ``(item, spec)``；``spec`` 仅对 ``select`` 类型有意义。
_REQUIRED_VALIDATORS: dict[str, Callable[[Any, ParamSpec], bool]] = {
    "text": lambda item, spec: _text(item),
    "number": lambda item, spec: _number(item, spec.minimum),
    "bool": lambda item, spec: _bool(item),
    "value": lambda item, spec: _text(item) or isinstance(item, bool),
    "list": lambda item, spec: _list_of_text(item),
    "actions": lambda item, spec: _list_of_actions(item),
    "select": lambda item, spec: _text(item) and str(item) in spec.options,
}

_OPTIONAL_VALIDATORS: dict[str, Callable[[Any, ParamSpec], bool]] = {
    "text": lambda item, spec: isinstance(item, str),
    "number": lambda item, spec: _number(item, spec.minimum),
    "bool": lambda item, spec: _bool(item),
    "value": lambda item, spec: isinstance(item, (str, bool)),
    "list": lambda item, spec: _list_of_text(item, allow_empty=True),
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
            ParamSpec(
                "texts",
                "目标文本",
                "list",
                required=True,
                placeholder="例如：签到,领取",
            ),
            ParamSpec(
                "skip_if_texts", "跳过条件", "list", placeholder="例如：已签到,已领取"
            ),
            ParamSpec(
                "match_mode",
                "匹配方式",
                "select",
                default="exact",
                options=TEXT_MATCH_MODES,
            ),
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
            ParamSpec(
                "texts",
                "需要 OCR 确认的文本",
                "list",
                required=True,
                placeholder="例如：签到,领取",
            ),
            ParamSpec(
                "skip_if_texts", "跳过条件", "list", placeholder="例如：已签到,已领取"
            ),
            ParamSpec(
                "match_mode",
                "匹配方式",
                "select",
                default="exact",
                options=TEXT_MATCH_MODES,
            ),
            *_polling_specs(15, 0.5),
        )
    ),
    "coordinate": _with_retries(
        (
            ParamSpec("x", "X 坐标", "number", required=True, minimum=0),
            ParamSpec("y", "Y 坐标", "number", required=True, minimum=0),
        )
    ),
}


def _detect_text_specs(result_var_hint: str) -> tuple[ParamSpec, ...]:
    """detect 的 ocr / ui_text 定位只差结果变量提示文案。"""
    return (
        ParamSpec(
            "texts", "目标文本", "list", required=True, placeholder="例如：签到,领取"
        ),
        ParamSpec(
            "result_var", "结果变量", "text", required=True, placeholder=result_var_hint
        ),
        ParamSpec(
            "match_mode",
            "匹配方式",
            "select",
            default="exact",
            options=TEXT_MATCH_MODES,
        ),
        *_polling_specs(30, 1),
        ParamSpec("continue_on_timeout", "超时继续", "bool", default=False),
    )


DETECT_SPECS: dict[str, tuple[ParamSpec, ...]] = {
    "ocr": _with_retries(_detect_text_specs("例如：ocr_state")),
    "ui_text": _with_retries(_detect_text_specs("例如：ui_state")),
    "ui_resource_id": _with_retries(
        (
            ParamSpec("resource_id", "Resource ID", "text", required=True),
            ParamSpec(
                "result_var",
                "结果变量",
                "text",
                required=True,
                placeholder="例如：ui_state",
            ),
            *_polling_specs(30, 1),
            ParamSpec("continue_on_timeout", "超时继续", "bool", default=False),
        )
    ),
}

IF_SPECS = _with_retries(
    (
        ParamSpec(
            "var", "条件变量", "text", required=True, placeholder="例如：ocr_state"
        ),
        ParamSpec(
            "equals", "等于", "value", required=True, placeholder="例如：领取 或 true"
        ),
        ParamSpec("then", "成立时步骤", "actions"),
        ParamSpec("else", "不成立时步骤", "actions"),
    )
)

LOOP_UNTIL_SPECS = _with_retries(
    (
        ParamSpec("var", "条件变量", "text", required=True, placeholder="例如：state"),
        ParamSpec("equals", "等于", "value", required=True, placeholder="例如：true"),
        ParamSpec("max_iterations", "最大次数", "number", default=1, minimum=1),
        ParamSpec("steps", "循环步骤", "actions"),
    )
)

LIFECYCLE_SPECS: dict[str, tuple[ParamSpec, ...]] = {
    "stop": _with_retries(
        (ParamSpec("package", "目标应用包名", "text", placeholder="留空使用任务包名"),)
    ),
    "launch": _with_retries(
        (
            ParamSpec(
                "package", "目标应用包名", "text", placeholder="留空使用任务包名"
            ),
            ParamSpec("wait_seconds", "启动后等待(秒)", "number", default=3, minimum=0),
            ParamSpec(
                "launch_attempts", "启动尝试次数", "number", default=3, minimum=1
            ),
        )
    ),
    "wait": _with_retries(
        (ParamSpec("seconds", "等待秒数", "number", default=1, minimum=0),)
    ),
    "back": _with_retries(()),
}

# 滑动起点/终点/时长参数：swipe 与 swipe_until 共用一份。
_SWIPE_COMMON_PARAMS = (
    ParamSpec("x1", "起点 X", "number", required=True, minimum=0),
    ParamSpec("y1", "起点 Y", "number", required=True, minimum=0),
    ParamSpec("x2", "终点 X", "number", required=True, minimum=0),
    ParamSpec("y2", "终点 Y", "number", required=True, minimum=0),
    ParamSpec("duration_ms", "时长(毫秒)", "number", default=300, minimum=0),
)

SWIPE_SPECS = _with_retries(_SWIPE_COMMON_PARAMS)

# swipe_until 的 ocr / ui_text 定位规格完全相同。
_SWIPE_UNTIL_TEXT_SPECS = _SWIPE_COMMON_PARAMS + (
    ParamSpec(
        "texts", "目标文本", "list", required=True, placeholder="例如：签到,领取"
    ),
    ParamSpec("result_var", "结果变量", "text", default="_swipe_until_state"),
    ParamSpec(
        "match_mode", "匹配方式", "select", default="exact", options=TEXT_MATCH_MODES
    ),
    *_polling_specs(8, 1),
    ParamSpec("continue_on_timeout", "超时继续", "bool", default=True),
    ParamSpec("max_iterations", "最大滑动次数", "number", default=5, minimum=1),
)

SWIPE_UNTIL_SPECS: dict[str, tuple[ParamSpec, ...]] = {
    "ocr": _with_retries(_SWIPE_UNTIL_TEXT_SPECS),
    "ui_text": _with_retries(_SWIPE_UNTIL_TEXT_SPECS),
    "ui_resource_id": _with_retries(
        _SWIPE_COMMON_PARAMS
        + (
            ParamSpec("resource_id", "Resource ID", "text", required=True),
            ParamSpec("result_var", "结果变量", "text", default="_swipe_until_state"),
            *_polling_specs(8, 1),
            ParamSpec("continue_on_timeout", "超时继续", "bool", default=True),
            ParamSpec("max_iterations", "最大滑动次数", "number", default=5, minimum=1),
        )
    ),
}

SCREENSHOT_SPECS = _with_retries(())


# 静态动作类型到参数规格的查表。click/detect 是动态解析的，
# 因为它们的规格依赖 locate/target 参数；其余类型均为固定规格。
_TYPE_SPECS: dict[str, tuple[ParamSpec, ...]] = {
    "swipe": SWIPE_SPECS,
    "if": IF_SPECS,
    "loop_until": LOOP_UNTIL_SPECS,
    "capture_screenshot": SCREENSHOT_SPECS,
    **LIFECYCLE_SPECS,
}


# 动态解析规格的动作类型：规格依赖 locate/target 参数。
_LOCATE_ACTION_SPECS: dict[str, dict[str, tuple[ParamSpec, ...]]] = {
    "click": CLICK_SPECS,
    "detect": DETECT_SPECS,
    "swipe_until": SWIPE_UNTIL_SPECS,
}


def _locatespecs_for(action_type: str, params: dict[str, Any]) -> tuple[ParamSpec, ...]:
    specs = _LOCATE_ACTION_SPECS[action_type]
    locate, target = resolve_locate_target(action_type, params)
    if locate == "ui":
        return specs.get(f"ui_{target}", specs["ui_text"])
    return specs.get(locate, specs["ocr"])


def specs_for(action_type: str, params: dict[str, Any]) -> tuple[ParamSpec, ...]:
    if action_type in _LOCATE_ACTION_SPECS:
        return _locatespecs_for(action_type, params)
    return _TYPE_SPECS.get(action_type, ())


def _allowed_parameter_keys(action_type: str) -> set[str]:
    specs = _LOCATE_ACTION_SPECS.get(action_type)
    if specs is not None:
        keys: set[str] = {"locate", "target"}
        for variants in specs.values():
            keys.update(spec.key for spec in variants)
        return keys
    return {spec.key for spec in specs_for(action_type, {})}


def resolve_locate_target(action_type: str, params: dict[str, Any]) -> tuple[str, str]:
    """解析 click/detect/swipe_until 使用的 locate（及 ui target）默认值。

    返回 ``(locate, target)``。``locate`` 解析为 ``"ui"`` 时 target 默认
    ``"text"``；否则 target 保持原始字符串（默认 ``""``），因为对非 ui
    定位而言它没有意义。
    """
    if action_type not in _LOCATE_ACTION_SPECS:
        return "", ""
    default_locate = "ui" if action_type == "click" else "ocr"
    # 显式 null 与缺失同义（回退默认）；非字符串值仍会经校验报错，不静默吞掉。
    raw_locate = params.get("locate")
    locate = default_locate if raw_locate is None else str(raw_locate)
    if locate == "ui":
        raw_target = params.get("target")
        target = "text" if raw_target is None else str(raw_target)
    else:
        raw_target = params.get("target")
        target = "" if raw_target is None else str(raw_target)
    return locate, target


def _validate_locate_target(
    action_type: str, params: dict[str, Any], errors: list[str]
) -> None:
    """校验 click/detect/swipe_until 的 locate/target 组合。"""

    if action_type not in _LOCATE_ACTION_SPECS:
        return
    locate, target = resolve_locate_target(action_type, params)
    locates, ui_targets = (
        (CLICK_LOCATES, CLICK_UI_TARGETS)
        if action_type == "click"
        else (DETECT_LOCATES, DETECT_TARGETS)
    )
    if locate not in locates:
        errors.append(f"{action_type} 的 locate 必须是 {'/'.join(locates)} 之一")
    elif locate == "ui" and target not in ui_targets:
        errors.append(f"{action_type} ui 的 target 必须是 {'/'.join(ui_targets)} 之一")


def validate_action_params(action_type: str, params: dict[str, Any]) -> list[str]:
    """返回一个动作的校验错误列表（合法时为空）。"""

    if action_type == "compound":
        errors = []
        name = params.get("name")
        if not _text(name):
            errors.append("复合动作缺少 name")
        retries = params.get("retries")
        if retries is not None and not _number(retries, 0):
            errors.append("重试次数（retries）必须是非负数字")
        unknown = [
            key for key in params if key not in {"name", "retries", "description"}
        ]
        if unknown:
            errors.append(
                f"compound 不支持参数: {', '.join(str(key) for key in sorted(unknown))}"
            )
        return errors
    if action_type not in PRIMITIVE_TYPE_SET:
        return [f"未知动作类型: {action_type}"]

    errors = []
    _validate_locate_target(action_type, params, errors)
    for spec in specs_for(action_type, params):
        value = params.get(spec.key)
        if spec.required:
            # 缺失 → 必填项；存在但类型/取值非法 → 格式不正确（避免误导）。
            if value is None:
                errors.append(f"{spec.label}（{spec.key}）为必填项")
            elif not _REQUIRED_VALIDATORS[spec.kind](value, spec):
                errors.append(f"{spec.label}（{spec.key}）格式不正确")
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
        errors.append(
            f"{action_type} 不支持参数: {', '.join(str(key) for key in sorted(unknown))}"
        )
    return errors


def effective_parameters(action_type: str, params: dict[str, Any]) -> dict[str, Any]:
    """把文档声明的默认值填入参数，不覆盖用户已设置的值（显式 null 视同未设置）。"""

    effective = dict(params)
    if action_type in ("click", "detect", "swipe_until"):
        locate, target = resolve_locate_target(action_type, effective)
        # 显式 null 也覆盖为解析出的默认值，保证校验与执行读到同一形态。
        if effective.get("locate") is None:
            effective["locate"] = locate
        if effective.get("locate") == "ui" and effective.get("target") is None:
            effective["target"] = target
    for spec in specs_for(action_type, effective):
        if spec.default is not None and effective.get(spec.key) is None:
            effective[spec.key] = spec.default
    return effective


def _join_text_values(value: Any) -> str:
    """把文本目标（单个字符串或列表）渲染成便于阅读的文本。"""

    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value) or "-"
    text = str(value).strip()
    return text or "-"


def describe_action(action_type: str, params: dict[str, Any]) -> str:
    """生成用于日志和编辑器的简短可读描述。"""

    effective = effective_parameters(action_type, params)
    if action_type == "click":
        locate = str(effective.get("locate", "ui"))
        if locate == "ui":
            target = str(effective.get("target", "text"))
            if target == "text":
                suffix = "（模糊）" if effective.get("match_mode") == "fuzzy" else ""
                return (
                    f"点击文本: {_join_text_values(effective.get('texts', ''))}{suffix}"
                )
            return f"点击控件: {effective.get('resource_id', '')}"
        if locate == "ocr":
            suffix = "（模糊）" if effective.get("match_mode") == "fuzzy" else ""
            return (
                f"OCR 点击文本: {_join_text_values(effective.get('texts', ''))}{suffix}"
            )
        return f"点击坐标: ({effective.get('x', '')}, {effective.get('y', '')})"
    if action_type == "swipe":
        return "滑动"
    if action_type == "swipe_until":
        locate = str(effective.get("locate", "ocr"))
        if locate == "ui":
            target = str(effective.get("target", "text"))
            if target == "resource_id":
                return f"滑动直到 UI 控件: {effective.get('resource_id', '')}"
            suffix = "（模糊）" if effective.get("match_mode") == "fuzzy" else ""
            return f"滑动直到 UI 文本: {_join_text_values(effective.get('texts', ''))}{suffix}"
        suffix = "（模糊）" if effective.get("match_mode") == "fuzzy" else ""
        return f"滑动直到 OCR 文本: {_join_text_values(effective.get('texts', ''))}{suffix}"
    if action_type == "detect":
        locate = str(effective.get("locate", "ocr"))
        result_var = str(effective.get("result_var", ""))
        if locate == "ui":
            target = str(effective.get("target", "text"))
            if target == "resource_id":
                return (
                    f"UI 检测控件: {effective.get('resource_id', '')} -> {result_var}"
                )
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
