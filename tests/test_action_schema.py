from __future__ import annotations

import unittest

from free_app.action_schema import describe_action, effective_parameters, validate_action_params


class ActionSchemaTests(unittest.TestCase):
    def test_describe_if_with_boolean_equals(self) -> None:
        description = describe_action("if", {"var": "state_found", "equals": False})

        self.assertIn("state_found == false", description)

    def test_validation_reports_invalid_click_locations_and_targets(self) -> None:
        self.assertTrue(validate_action_params("click", {"locate": "bad"}))
        self.assertTrue(
            validate_action_params(
                "click",
                {"locate": "ui", "target": "bad", "text": "领取"},
            )
        )

    def test_validation_reports_invalid_detect_locations_and_targets(self) -> None:
        self.assertTrue(
            validate_action_params(
                "detect",
                {"locate": "bad", "texts": ["领取"], "result_var": "state"},
            )
        )
        self.assertTrue(
            validate_action_params(
                "detect",
                {
                    "locate": "ui",
                    "target": "bad",
                    "texts": ["领取"],
                    "result_var": "state",
                },
            )
        )

    def test_validation_reports_invalid_if_and_unknown_parameters(self) -> None:
        self.assertTrue(validate_action_params("if", {"var": "state"}))
        self.assertTrue(validate_action_params("unknown_action", {}))

    def test_click_text_accepts_multi_targets_and_legacy_string(self) -> None:
        self.assertEqual(
            validate_action_params("click", {"locate": "ui", "text": ["签到", "领取"]}),
            [],
        )
        self.assertEqual(
            validate_action_params("click", {"locate": "ui", "text": "签到"}),
            [],
        )

    def test_describe_click_joins_multiple_text_targets(self) -> None:
        self.assertEqual(
            describe_action("click", {"locate": "ui", "text": ["签到", "领取"]}),
            "点击文本: 签到,领取",
        )

    def test_validation_rejects_unsupported_action_parameters(self) -> None:
        self.assertTrue(
            validate_action_params(
                "click",
                {"locate": "ui", "text": "领取", "stale_param": "x"},
            )
        )
        self.assertTrue(validate_action_params("back", {"stale_param": 1}))
        self.assertTrue(validate_action_params("wait", {"seconds": 1, "stale_param": 1}))
        self.assertTrue(
            validate_action_params(
                "detect",
                {
                    "locate": "ocr",
                    "texts": ["领取"],
                    "result_var": "state",
                    "stale_param": True,
                },
            )
        )
        self.assertTrue(
            validate_action_params(
                "swipe",
                {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "stale_param": 2},
            )
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

    def test_validation_reports_bad_nested_steps(self) -> None:
        errors = validate_action_params(
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
        )

        self.assertTrue(errors)

    def test_effective_parameters_fill_click_and_detect_defaults(self) -> None:
        click = effective_parameters("click", {})
        self.assertEqual(click["locate"], "ui")
        self.assertEqual(click["target"], "text")
        self.assertEqual(click["match_mode"], "exact")

        detect = effective_parameters("detect", {"result_var": "state"})
        self.assertEqual(detect["locate"], "ocr")
        self.assertEqual(detect["match_mode"], "exact")
        self.assertEqual(detect["timeout_seconds"], 30)

    def test_describe_action_covers_click_and_detect_variants(self) -> None:
        self.assertIn("点击文本", describe_action("click", {"text": "领取"}))
        self.assertIn(
            "点击控件",
            describe_action(
                "click",
                {"locate": "ui", "target": "resource_id", "resource_id": "com.demo:id/btn"},
            ),
        )
        self.assertIn("OCR", describe_action("click", {"locate": "ocr", "text": "领取"}))
        self.assertIn(
            "坐标",
            describe_action("click", {"locate": "coordinate", "x": 1, "y": 2}),
        )
        self.assertIn("OCR", describe_action("detect", {"texts": ["领取"], "result_var": "s"}))
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
        self.assertIn("state == 领取", describe_action("if", {"var": "state", "equals": "领取"}))
        self.assertIn("state_found == True", describe_action("loop_until", {"var": "state_found", "equals": True}))
        self.assertIn("退出应用", describe_action("stop", {}))
        self.assertIn("启动应用", describe_action("launch", {}))
        self.assertIn("等待 2 秒", describe_action("wait", {"seconds": 2}))
        self.assertIn("返回", describe_action("back", {}))
        self.assertIn("截图", describe_action("capture_screenshot", {"label": "成功"}))
        self.assertIn("复合动作", describe_action("compound", {"name": "demo"}))


if __name__ == "__main__":
    unittest.main()
