from __future__ import annotations

import unittest
from typing import Any

from free_app.action_schema import (
    CLICK_SPECS,
    DETECT_SPECS,
    IF_SPECS,
    LIFECYCLE_SPECS,
    LOOP_UNTIL_SPECS,
    RETRIES_SPEC,
    SWIPE_SPECS,
    SWIPE_UNTIL_SPECS,
    ParamSpec,
    describe_action,
    effective_parameters,
    validate_action_params,
)


def _by_key(specs: tuple[ParamSpec, ...]) -> dict[str, ParamSpec]:
    """按参数键索引 spec 元组，便于断言易读。"""

    return {spec.key: spec for spec in specs}


class ActionSchemaTests(unittest.TestCase):
    def test_describe_if_with_boolean_equals(self) -> None:
        description = describe_action("if", {"var": "state_found", "equals": False})

        self.assertIn("state_found == false", description)

    def test_validation_reports_invalid_click_locations_and_targets(self) -> None:
        self.assertEqual(
            validate_action_params("click", {"locate": "bad", "texts": ["领取"]}),
            ["click 的 locate 必须是 ui/ocr/coordinate 之一"],
        )
        self.assertEqual(
            validate_action_params(
                "click",
                {"locate": "ui", "target": "bad", "texts": ["领取"]},
            ),
            ["click ui 的 target 必须是 text/resource_id 之一"],
        )

    def test_validation_reports_invalid_detect_locations_and_targets(self) -> None:
        self.assertEqual(
            validate_action_params(
                "detect",
                {"locate": "bad", "texts": ["领取"], "result_var": "state"},
            ),
            ["detect 的 locate 必须是 ocr/ui 之一"],
        )
        self.assertEqual(
            validate_action_params(
                "detect",
                {
                    "locate": "ui",
                    "target": "bad",
                    "texts": ["领取"],
                    "result_var": "state",
                },
            ),
            ["detect ui 的 target 必须是 text/resource_id 之一"],
        )

    def test_validation_reports_invalid_if_and_unknown_parameters(self) -> None:
        self.assertEqual(
            validate_action_params("if", {"var": "state"}),
            ["等于（equals）为必填项"],
        )
        self.assertEqual(
            validate_action_params("unknown_action", {}),
            ["未知动作类型: unknown_action"],
        )

    def test_click_texts_must_be_a_list_of_non_blank_strings(self) -> None:
        self.assertEqual(
            validate_action_params(
                "click", {"locate": "ui", "texts": ["签到", "领取"]}
            ),
            [],
        )
        # 键存在但值非法 → 格式不正确；键缺失/显式 None → 为必填项。
        for invalid in ("签到", [], ["签到", "  "], 12):
            with self.subTest(texts=invalid):
                self.assertEqual(
                    validate_action_params("click", {"locate": "ui", "texts": invalid}),
                    ["目标文本（texts）格式不正确"],
                )
        self.assertEqual(
            validate_action_params("click", {"locate": "ui", "texts": None}),
            ["目标文本（texts）为必填项"],
        )
        self.assertEqual(
            validate_action_params("click", {"locate": "ui"}),
            ["目标文本（texts）为必填项"],
        )

    def test_legacy_click_text_key_is_no_longer_supported(self) -> None:
        self.assertEqual(
            validate_action_params("click", {"locate": "ui", "text": "领取"}),
            ["目标文本（texts）为必填项", "click 不支持参数: text"],
        )

    def test_skip_if_texts_may_be_omitted_or_empty(self) -> None:
        base: dict[str, Any] = {"locate": "ui", "texts": ["领取"]}
        for skip_if_texts in (["已签到"], []):
            with self.subTest(skip_if_texts=skip_if_texts):
                self.assertEqual(
                    validate_action_params(
                        "click", {**base, "skip_if_texts": skip_if_texts}
                    ),
                    [],
                )
        for invalid in ("已签到", [""], ["已签到", "  "], 5):
            with self.subTest(skip_if_texts=invalid):
                self.assertEqual(
                    validate_action_params("click", {**base, "skip_if_texts": invalid}),
                    ["跳过条件（skip_if_texts）格式不正确"],
                )

    def test_ocr_and_swipe_until_texts_are_lists_too(self) -> None:
        self.assertEqual(
            validate_action_params(
                "click", {"locate": "ocr", "texts": ["签到", "领取"]}
            ),
            [],
        )
        self.assertEqual(
            validate_action_params(
                "swipe_until",
                {"x1": 0, "y1": 1, "x2": 0, "y2": 300, "texts": "签到"},
            ),
            ["目标文本（texts）格式不正确"],
        )
        self.assertEqual(
            validate_action_params(
                "swipe_until",
                {"x1": 0, "y1": 1, "x2": 0, "y2": 300, "locate": "ocr"},
            ),
            ["目标文本（texts）为必填项"],
        )

    def test_validation_rejects_unsupported_action_parameters(self) -> None:
        self.assertEqual(
            validate_action_params(
                "click",
                {"locate": "ui", "texts": ["领取"], "stale_param": "x"},
            ),
            ["click 不支持参数: stale_param"],
        )
        self.assertEqual(
            validate_action_params("back", {"stale_param": 1}),
            ["back 不支持参数: stale_param"],
        )
        self.assertEqual(
            validate_action_params("back", {"zeta": 1, "alpha": 2}),
            ["back 不支持参数: alpha, zeta"],
        )
        self.assertEqual(
            validate_action_params("wait", {"seconds": 1, "stale_param": 1}),
            ["wait 不支持参数: stale_param"],
        )
        self.assertEqual(
            validate_action_params(
                "detect",
                {
                    "locate": "ocr",
                    "texts": ["领取"],
                    "result_var": "state",
                    "stale_param": True,
                },
            ),
            ["detect 不支持参数: stale_param"],
        )
        self.assertEqual(
            validate_action_params(
                "swipe",
                {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "stale_param": 2},
            ),
            ["swipe 不支持参数: stale_param"],
        )

    def test_retries_is_a_native_parameter_for_all_primitives(self) -> None:
        self.assertEqual(
            validate_action_params(
                "detect",
                {"texts": ["领取"], "result_var": "state", "retries": 2},
            ),
            [],
        )
        self.assertEqual(
            validate_action_params("back", {"retries": 1}),
            [],
        )

    def test_number_parameters_enforce_documented_minimums(self) -> None:
        cases: list[tuple[str, dict[str, Any], str]] = [
            (
                "click",
                {"locate": "ui", "texts": ["领取"], "retries": -1},
                "重试次数（retries）格式不正确",
            ),
            (
                "click",
                {"locate": "ui", "texts": ["领取"], "timeout_seconds": -0.5},
                "超时(秒)（timeout_seconds）格式不正确",
            ),
            (
                "click",
                {"locate": "ui", "texts": ["领取"], "interval_seconds": -1},
                "轮询间隔(秒)（interval_seconds）格式不正确",
            ),
            (
                "click",
                {"locate": "coordinate", "x": -1, "y": 2},
                "X 坐标（x）格式不正确",
            ),
            (
                "detect",
                {
                    "locate": "ocr",
                    "texts": ["领取"],
                    "result_var": "state",
                    "interval_seconds": -1,
                },
                "轮询间隔(秒)（interval_seconds）格式不正确",
            ),
            ("wait", {"seconds": -1}, "等待秒数（seconds）格式不正确"),
            (
                "launch",
                {"wait_seconds": -1},
                "启动后等待(秒)（wait_seconds）格式不正确",
            ),
            (
                "launch",
                {"launch_attempts": 0},
                "启动尝试次数（launch_attempts）格式不正确",
            ),
            ("swipe", {"x1": 0, "y1": 0, "x2": 0, "y2": -1}, "终点 Y（y2）格式不正确"),
            (
                "swipe",
                {"x1": 0, "y1": 0, "x2": 0, "y2": 1, "duration_ms": -1},
                "时长(毫秒)（duration_ms）格式不正确",
            ),
            (
                "loop_until",
                {"var": "s", "equals": True, "max_iterations": 0},
                "最大次数（max_iterations）格式不正确",
            ),
            (
                "swipe_until",
                {
                    "x1": 0,
                    "y1": 0,
                    "x2": 0,
                    "y2": 300,
                    "texts": ["签到"],
                    "max_iterations": 0,
                },
                "最大滑动次数（max_iterations）格式不正确",
            ),
        ]
        for action_type, params, message in cases:
            with self.subTest(action_type=action_type, params=params):
                self.assertEqual(validate_action_params(action_type, params), [message])

    def test_number_parameters_reject_non_finite_values(self) -> None:
        # inf/nan 与 helpers.number_setting 同边界：校验期即拒，
        # 绝不让无限 deadline/sleep 进入引擎。
        for value in (float("inf"), float("-inf"), float("nan")):
            with self.subTest(seconds=value):
                self.assertEqual(
                    validate_action_params("wait", {"seconds": value}),
                    ["等待秒数（seconds）格式不正确"],
                )
            self.assertEqual(
                validate_action_params(
                    "click",
                    {
                        "locate": "ui",
                        "texts": ["领取"],
                        "timeout_seconds": value,
                    },
                ),
                ["超时(秒)（timeout_seconds）格式不正确"],
            )

    def test_explicit_null_locate_and_target_fall_back_to_defaults(self) -> None:
        # 显式 null 与缺失同义：校验回退默认定位，effective_parameters 写入
        # 解析后的默认值，引擎读到的永远是字符串形态。
        self.assertEqual(
            validate_action_params("click", {"locate": None, "texts": ["领取"]}),
            [],
        )
        self.assertEqual(
            validate_action_params(
                "detect",
                {"locate": None, "texts": ["领取"], "result_var": "state"},
            ),
            [],
        )
        effective = effective_parameters(
            "click", {"locate": None, "target": None, "texts": ["领取"]}
        )
        self.assertEqual(effective["locate"], "ui")
        self.assertEqual(effective["target"], "text")
        # 非字符串脏类型仍要报错，不被默认值静默吞掉。
        self.assertEqual(
            validate_action_params("click", {"locate": 123, "texts": ["领取"]}),
            ["click 的 locate 必须是 ui/ocr/coordinate 之一"],
        )

    def test_minimum_bounds_are_inclusive(self) -> None:
        self.assertEqual(validate_action_params("wait", {"seconds": 0}), [])
        self.assertEqual(
            validate_action_params(
                "click",
                {
                    "locate": "ui",
                    "texts": ["领取"],
                    "retries": 0,
                    "timeout_seconds": 0,
                    "interval_seconds": 0,
                },
            ),
            [],
        )
        self.assertEqual(
            validate_action_params("click", {"locate": "coordinate", "x": 0, "y": 0}),
            [],
        )
        self.assertEqual(
            validate_action_params("launch", {"wait_seconds": 0, "launch_attempts": 1}),
            [],
        )
        self.assertEqual(
            validate_action_params(
                "loop_until", {"var": "s", "equals": True, "max_iterations": 1}
            ),
            [],
        )

    def test_number_parameters_reject_strings_and_booleans(self) -> None:
        # 字符串数字不再被宽容转换：非 int/float 一律在校验期失败。
        for value in ("15", True, [15], {}):
            with self.subTest(seconds=value):
                self.assertEqual(
                    validate_action_params("wait", {"seconds": value}),
                    ["等待秒数（seconds）格式不正确"],
                )
        self.assertEqual(
            validate_action_params(
                "detect",
                {
                    "locate": "ocr",
                    "texts": ["领取"],
                    "result_var": "state",
                    "timeout_seconds": "30",
                },
            ),
            ["超时(秒)（timeout_seconds）格式不正确"],
        )
        self.assertEqual(
            validate_action_params("swipe", {"x1": "0", "y1": 0, "x2": 1, "y2": 1}),
            ["起点 X（x1）格式不正确"],
        )

    def test_required_parameters_distinguish_missing_from_bad_type(self) -> None:
        # 缺失 → "为必填项"；存在但类型非法 → "格式不正确"。
        self.assertEqual(
            validate_action_params("detect", {"locate": "ocr", "texts": ["领取"]}),
            ["结果变量（result_var）为必填项"],
        )
        self.assertEqual(
            validate_action_params(
                "detect",
                {
                    "locate": "ui",
                    "target": "resource_id",
                    "resource_id": 5,
                    "result_var": "state",
                },
            ),
            ["Resource ID（resource_id）格式不正确"],
        )
        self.assertEqual(
            validate_action_params("click", {"locate": "coordinate", "x": 1}),
            ["Y 坐标（y）为必填项"],
        )

    def test_equals_accepts_text_and_bool_only(self) -> None:
        self.assertEqual(
            validate_action_params("if", {"var": "s", "equals": "领取"}), []
        )
        self.assertEqual(validate_action_params("if", {"var": "s", "equals": True}), [])
        self.assertEqual(
            validate_action_params("if", {"var": "s", "equals": 5}),
            ["等于（equals）格式不正确"],
        )
        self.assertEqual(
            validate_action_params("if", {"var": "s"}),
            ["等于（equals）为必填项"],
        )

    def test_compound_retries_must_be_a_non_negative_number(self) -> None:
        self.assertEqual(validate_action_params("compound", {"name": "demo"}), [])
        self.assertEqual(
            validate_action_params("compound", {"name": "demo", "retries": 3}), []
        )
        for invalid in (-1, "2", True):
            with self.subTest(retries=invalid):
                self.assertEqual(
                    validate_action_params(
                        "compound", {"name": "demo", "retries": invalid}
                    ),
                    ["重试次数（retries）必须是非负数字"],
                )

    def test_compound_only_accepts_name_retries_and_description(self) -> None:
        self.assertEqual(
            validate_action_params("compound", {"steps": []}),
            ["复合动作缺少 name", "compound 不支持参数: steps"],
        )
        self.assertEqual(
            validate_action_params(
                "compound", {"name": "demo", "description": "说明", "retries": 0}
            ),
            [],
        )

    def test_validation_reports_bad_nested_steps(self) -> None:
        self.assertEqual(
            validate_action_params(
                "if",
                {
                    "var": "state",
                    "equals": True,
                    "then": [
                        {"type": "unknown"},
                        {"text": "missing type"},
                        123,
                        {"type": "click", "locate": "bad"},
                    ],
                },
            ),
            [
                "if then[0] 未知动作类型: unknown",
                "if then[1] 必须是带 type 的动作对象",
                "if then[2] 必须是带 type 的动作对象",
                "if then[3]: click 的 locate 必须是 ui/ocr/coordinate 之一",
                "if then[3]: 需要 OCR 确认的文本（texts）为必填项",
            ],
        )
        self.assertEqual(
            validate_action_params(
                "loop_until",
                {
                    "var": "state",
                    "equals": True,
                    "steps": [{"type": "wait", "seconds": -1}],
                },
            ),
            ["loop_until steps[0]: 等待秒数（seconds）格式不正确"],
        )
        self.assertEqual(
            validate_action_params(
                "if", {"var": "state", "equals": True, "then": "back"}
            ),
            ["成立时步骤（then）格式不正确"],
        )

    def test_effective_parameters_fill_click_and_detect_defaults(self) -> None:
        click = effective_parameters("click", {})
        self.assertEqual(click["locate"], "ui")
        self.assertEqual(click["target"], "text")
        self.assertEqual(click["match_mode"], "exact")
        self.assertEqual(click["timeout_seconds"], 15)
        self.assertEqual(click["interval_seconds"], 0.5)
        self.assertEqual(click["retries"], 0)
        # 无 ParamSpec 默认值的可选参数不会被凭空创建。
        self.assertNotIn("skip_if_texts", click)
        self.assertNotIn("texts", click)

        detect = effective_parameters("detect", {"result_var": "state"})
        self.assertEqual(detect["locate"], "ocr")
        self.assertEqual(detect["match_mode"], "exact")
        self.assertEqual(detect["timeout_seconds"], 30)
        self.assertEqual(detect["interval_seconds"], 1)
        self.assertIs(detect["continue_on_timeout"], False)
        self.assertEqual(detect["retries"], 0)

        self.assertEqual(effective_parameters("wait", {})["seconds"], 1)
        self.assertEqual(effective_parameters("back", {})["retries"], 0)
        launch = effective_parameters("launch", {})
        self.assertEqual(launch["wait_seconds"], 3)
        self.assertEqual(launch["launch_attempts"], 3)

    def test_effective_parameters_never_override_user_values(self) -> None:
        click = effective_parameters(
            "click",
            {
                "texts": ["领取"],
                "retries": 0,
                "timeout_seconds": 0,
                "match_mode": "fuzzy",
            },
        )
        self.assertEqual(click["retries"], 0)
        self.assertEqual(click["timeout_seconds"], 0)
        self.assertEqual(click["match_mode"], "fuzzy")
        swipe = effective_parameters("swipe_until", {"result_var": ""})
        self.assertEqual(swipe["result_var"], "")

    def test_swipe_until_validation_and_effective_parameters(self) -> None:
        self.assertEqual(
            validate_action_params(
                "swipe_until",
                {
                    "x1": 0,
                    "y1": 1,
                    "x2": 0,
                    "y2": 300,
                    "duration_ms": 800,
                    "locate": "ocr",
                    "texts": ["签到"],
                    "result_var": "state",
                },
            ),
            [],
        )
        self.assertEqual(
            validate_action_params(
                "swipe_until",
                {
                    "x1": 0,
                    "y1": 1,
                    "x2": 0,
                    "y2": 300,
                    "locate": "ui",
                    "target": "resource_id",
                    "resource_id": "com.demo:id/btn",
                    "result_var": "state",
                },
            ),
            [],
        )
        # result_var 现在是可省略的（默认 _swipe_until_state）。
        self.assertEqual(
            validate_action_params(
                "swipe_until",
                {
                    "x1": 0,
                    "y1": 1,
                    "x2": 0,
                    "y2": 300,
                    "locate": "ocr",
                    "texts": ["签到"],
                },
            ),
            [],
        )
        self.assertEqual(
            validate_action_params(
                "swipe_until",
                {"locate": "bad", "texts": ["签到"], "result_var": "state"},
            ),
            [
                "swipe_until 的 locate 必须是 ocr/ui 之一",
                "起点 X（x1）为必填项",
                "起点 Y（y1）为必填项",
                "终点 X（x2）为必填项",
                "终点 Y（y2）为必填项",
            ],
        )

        swipe_until = effective_parameters(
            "swipe_until",
            {"x1": 0, "y1": 1, "x2": 0, "y2": 300, "texts": ["签到"]},
        )
        self.assertEqual(swipe_until["locate"], "ocr")
        self.assertEqual(swipe_until.get("target"), None)
        self.assertEqual(swipe_until["duration_ms"], 300)
        self.assertEqual(swipe_until["result_var"], "_swipe_until_state")
        self.assertEqual(swipe_until["match_mode"], "exact")
        self.assertEqual(swipe_until["timeout_seconds"], 8)
        self.assertEqual(swipe_until["interval_seconds"], 1)
        self.assertEqual(swipe_until["max_iterations"], 5)
        self.assertIs(swipe_until["continue_on_timeout"], True)
        self.assertEqual(swipe_until["retries"], 0)

        ui = effective_parameters(
            "swipe_until",
            {"x1": 0, "y1": 1, "x2": 0, "y2": 300, "locate": "ui", "texts": ["签到"]},
        )
        self.assertEqual(ui["target"], "text")
        self.assertEqual(ui["result_var"], "_swipe_until_state")

    def test_describe_click_joins_multiple_text_targets(self) -> None:
        self.assertEqual(
            describe_action("click", {"locate": "ui", "texts": ["签到", "领取"]}),
            "点击文本: 签到,领取",
        )
        self.assertEqual(
            describe_action(
                "click", {"locate": "ui", "texts": ["签到"], "match_mode": "fuzzy"}
            ),
            "点击文本: 签到（模糊）",
        )
        self.assertEqual(describe_action("click", {}), "点击文本: -")

    def test_describe_swipe_until_variants(self) -> None:
        self.assertIn(
            "滑动直到 OCR 文本",
            describe_action(
                "swipe_until",
                {"texts": ["签到"], "result_var": "state"},
            ),
        )
        self.assertIn(
            "滑动直到 UI 文本",
            describe_action(
                "swipe_until",
                {"locate": "ui", "texts": ["签到"], "result_var": "state"},
            ),
        )
        self.assertIn(
            "滑动直到 UI 控件",
            describe_action(
                "swipe_until",
                {
                    "locate": "ui",
                    "target": "resource_id",
                    "resource_id": "com.demo:id/btn",
                    "result_var": "state",
                },
            ),
        )

    def test_describe_action_covers_click_and_detect_variants(self) -> None:
        self.assertIn("点击文本", describe_action("click", {"texts": ["领取"]}))
        self.assertIn(
            "点击控件",
            describe_action(
                "click",
                {
                    "locate": "ui",
                    "target": "resource_id",
                    "resource_id": "com.demo:id/btn",
                },
            ),
        )
        self.assertIn(
            "OCR", describe_action("click", {"locate": "ocr", "texts": ["领取"]})
        )
        self.assertIn(
            "坐标",
            describe_action("click", {"locate": "coordinate", "x": 1, "y": 2}),
        )
        self.assertIn(
            "OCR", describe_action("detect", {"texts": ["领取"], "result_var": "s"})
        )
        self.assertIn(
            "UI",
            describe_action(
                "detect",
                {"locate": "ui", "texts": ["领取"], "result_var": "s"},
            ),
        )
        self.assertIn(
            "控件",
            describe_action(
                "detect",
                {
                    "locate": "ui",
                    "target": "resource_id",
                    "resource_id": "com.demo:id/btn",
                    "result_var": "s",
                },
            ),
        )

    def test_describe_action_covers_remaining_primitive_types(self) -> None:
        self.assertIn(
            "state == 领取", describe_action("if", {"var": "state", "equals": "领取"})
        )
        self.assertIn(
            "state_found == True",
            describe_action("loop_until", {"var": "state_found", "equals": True}),
        )
        self.assertIn(
            "滑动直到",
            describe_action("swipe_until", {"texts": ["签到"], "result_var": "state"}),
        )
        self.assertIn("退出应用", describe_action("stop", {}))
        self.assertIn("启动应用", describe_action("launch", {}))
        self.assertIn("等待 2 秒", describe_action("wait", {"seconds": 2}))
        self.assertIn("返回", describe_action("back", {}))
        self.assertIn("截图", describe_action("capture_screenshot", {"label": "成功"}))
        self.assertIn("复合动作", describe_action("compound", {"name": "demo"}))

    def test_number_specs_declare_their_minimums(self) -> None:
        self.assertEqual(RETRIES_SPEC.kind, "number")
        self.assertEqual(RETRIES_SPEC.minimum, 0)
        self.assertEqual(RETRIES_SPEC.default, 0)

        click = _by_key(CLICK_SPECS["ui_text"])
        self.assertEqual(click["texts"].kind, "list")
        self.assertTrue(click["texts"].required)
        self.assertIsNone(click["texts"].minimum)
        self.assertEqual(click["skip_if_texts"].minimum, None)
        self.assertEqual(click["timeout_seconds"].minimum, 0)
        self.assertEqual(click["interval_seconds"].minimum, 0)
        self.assertEqual(_by_key(CLICK_SPECS["coordinate"])["x"].minimum, 0)

        detect = _by_key(DETECT_SPECS["ocr"])
        self.assertTrue(detect["result_var"].required)
        self.assertEqual(detect["continue_on_timeout"].default, False)

        launch = _by_key(LIFECYCLE_SPECS["launch"])
        self.assertEqual(launch["wait_seconds"].minimum, 0)
        self.assertEqual(launch["wait_seconds"].default, 3)
        self.assertEqual(launch["launch_attempts"].minimum, 1)

        swipe_until = _by_key(SWIPE_UNTIL_SPECS["ui_text"])
        self.assertEqual(swipe_until["result_var"].default, "_swipe_until_state")
        self.assertEqual(swipe_until["max_iterations"].minimum, 1)
        self.assertEqual(swipe_until["continue_on_timeout"].default, True)
        self.assertEqual(_by_key(SWIPE_UNTIL_SPECS["ocr"])["x1"].minimum, 0)

        swipe = _by_key(SWIPE_SPECS)
        self.assertEqual(swipe["x1"].minimum, 0)
        self.assertEqual(swipe["duration_ms"].default, 300)

        loop = _by_key(LOOP_UNTIL_SPECS)
        self.assertEqual(loop["max_iterations"].minimum, 1)
        self.assertEqual(loop["max_iterations"].default, 1)
        self.assertEqual(_by_key(IF_SPECS)["equals"].kind, "value")


if __name__ == "__main__":
    unittest.main()
