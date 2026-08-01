from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from free_app.adb import AdbError, Device, ScreenInfo
from free_app.config import (
    expand_action_for_run,
    load_action_library,
    load_task_directory,
)
from free_app.constants import SCREEN_DENSITY, SCREEN_HEIGHT, SCREEN_WIDTH
from free_app.engine import ActionError, AutomationEngine, StopRequested
from free_app.models import Action, RunStatus, TaskDefinition


class FakeAdb:
    serial = "emulator-5556"

    def __init__(self) -> None:
        self.taps: list[tuple[int, int]] = []
        self.backs: list[int] = []
        self.swipes: list[tuple[int, int, int, int, int]] = []
        self.stopped: list[str] = []
        self.launched: list[str] = []
        self.dump_count = 0

    def select_device(self, _preferred: str | None = None) -> Device:
        return Device("emulator-5556", "device")

    def screen_info(self) -> ScreenInfo:
        return ScreenInfo(SCREEN_WIDTH, SCREEN_HEIGHT, SCREEN_DENSITY)

    def current_package(self) -> str | None:
        return self.launched[-1] if self.launched else None

    def reconnect(self) -> bool:
        return True

    def dump_ui(self) -> str:
        self.dump_count += 1
        enabled = "true" if self.dump_count == 1 else "false"
        clickable = "true" if self.dump_count == 1 else "false"
        return (
            '<hierarchy><node text="领取" class="android.widget.Button" '
            f'enabled="{enabled}" clickable="{clickable}" visible-to-user="true" '
            'bounds="[10,20][110,80]" /></hierarchy>'
        )

    def tap(self, x: int, y: int) -> None:
        self.taps.append((x, y))

    def press_back(self) -> None:
        self.backs.append(1)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.swipes.append((x1, y1, x2, y2, duration_ms))

    def force_stop(self, package: str) -> None:
        self.stopped.append(package)

    def launch(self, package: str) -> None:
        self.launched.append(package)

    def screenshot(self) -> bytes:
        return b"test screenshot"


class EngineTests(unittest.TestCase):
    def test_snapshot_logs_hierarchy_recognition_statistics(self) -> None:
        logs: list[str] = []
        engine = AutomationEngine(
            FakeAdb(),
            Path("screenshots"),
            log_callback=logs.append,
            poll_interval=0,
            sleep_function=lambda _seconds: None,
        )

        snapshot = engine._snapshot()

        self.assertEqual(len(snapshot.nodes), 2)
        self.assertTrue(any("UI hierarchy 识别成功" in message for message in logs))
        self.assertTrue(any("nodes=1" in message for message in logs))
        self.assertTrue(any("visible=1" in message for message in logs))

    def test_snapshot_logs_uiautomator_recognition_failure(self) -> None:
        class UnreadableAdb(FakeAdb):
            def dump_ui(self) -> str:
                raise AdbError("ADB 命令失败: ERROR: could not get idle state.")

        logs: list[str] = []
        engine = AutomationEngine(
            UnreadableAdb(),
            Path("screenshots"),
            log_callback=logs.append,
            poll_interval=0,
            sleep_function=lambda _seconds: None,
        )

        with self.assertRaises(ActionError):
            engine._snapshot()

        self.assertTrue(any("UI hierarchy 识别失败" in message for message in logs))
        self.assertTrue(any("could not get idle state" in message for message in logs))

    def test_snapshot_converts_malformed_xml_to_action_error(self) -> None:
        class MalformedXmlAdb(FakeAdb):
            def dump_ui(self) -> str:
                return "<hierarchy><node"

        logs: list[str] = []
        engine = AutomationEngine(
            MalformedXmlAdb(),
            Path("screenshots"),
            log_callback=logs.append,
            poll_interval=0,
            sleep_function=lambda _seconds: None,
        )

        with self.assertRaisesRegex(ActionError, "读取当前页面失败"):
            engine._snapshot()

        self.assertTrue(any("UI hierarchy 识别失败" in message for message in logs))

    def test_swipe_action_runs_single_gesture(self) -> None:
        adb = FakeAdb()
        sleeps: list[float] = []
        clock = [0.0]

        def monotonic() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock[0] += seconds

        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            poll_interval=0,
            sleep_function=sleep,
        )

        with patch("free_app.engine.time.monotonic", side_effect=monotonic):
            engine._execute(
                Action(
                    "swipe",
                    {
                        "x1": 540,
                        "y1": 1450,
                        "x2": 540,
                        "y2": 300,
                        "duration_ms": 800,
                    },
                )
        )

        self.assertEqual(adb.swipes, [(540, 1450, 540, 300, 800)])
        self.assertEqual(sleeps, [])

    def test_loop_until_stops_after_finding_target(self) -> None:
        adb = FakeAdb()

        def recognizer(_image: bytes) -> list[str]:
            return ["签到"] if len(adb.swipes) >= 2 else ["畅玩卡"]

        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            poll_interval=0,
            sleep_function=lambda _seconds: None,
            ocr_client=recognizer,
        )
        engine._execute(
            Action(
                "loop_until",
                {
                    "var": "state_found",
                    "equals": True,
                    "max_iterations": 6,
                    "steps": [
                        {
                            "type": "swipe",
                            "x1": 540,
                            "y1": 1450,
                            "x2": 540,
                            "y2": 300,
                            "duration_ms": 800,
                        },
                        {
                            "type": "detect",
                            "locate": "ocr",
                            "texts": ["签到"],
                            "result_var": "state",
                            "continue_on_timeout": True,
                            "timeout_seconds": 0.05,
                            "interval_seconds": 0,
                        },
                    ],
                },
            )
        )

        self.assertEqual(len(adb.swipes), 2)

    def test_loop_until_raises_after_max_swipes(self) -> None:
        adb = FakeAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            poll_interval=0,
            sleep_function=lambda _seconds: None,
            ocr_client=lambda _image: ["畅玩卡"],
        )
        with self.assertRaisesRegex(ActionError, "超过最大次数 6"):
            engine._execute(
                Action(
                    "loop_until",
                    {
                        "var": "state_found",
                        "equals": True,
                        "max_iterations": 6,
                        "steps": [
                            {
                                "type": "swipe",
                                "x1": 540,
                                "y1": 1450,
                                "x2": 540,
                                "y2": 300,
                                "duration_ms": 800,
                            },
                            {
                                "type": "detect",
                                "locate": "ocr",
                                "texts": ["签到"],
                                "result_var": "state",
                                "continue_on_timeout": True,
                                "timeout_seconds": 0.05,
                                "interval_seconds": 0,
                            },
                        ],
                    },
                )
            )
        self.assertEqual(len(adb.swipes), 6)

    def test_detect_tolerates_transient_dump_failure(self) -> None:
        class FlakyDumpAdb(FakeAdb):
            def dump_ui(self) -> str:
                self.dump_count += 1
                if self.dump_count == 1:
                    raise AdbError("UI dump 没有返回有效的 hierarchy XML")
                return (
                    '<hierarchy><node text="领取" clickable="true" enabled="true" '
                    'visible-to-user="true" bounds="[10,20][110,80]" /></hierarchy>'
                )

        adb = FlakyDumpAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            poll_interval=0,
            sleep_function=lambda _seconds: None,
        )

        engine._execute(
            Action(
                "detect",
                {
                    "locate": "ui",
                    "target": "text",
                    "texts": ["领取"],
                    "result_var": "state",
                    "timeout_seconds": 1,
                    "interval_seconds": 0,
                },
            )
        )

        self.assertEqual(engine._context["state"], "领取")

    def test_capture_screenshot_action_saves_key_screenshot(self) -> None:
        adb = FakeAdb()
        task = TaskDefinition(
            id="capture-demo",
            name="Capture demo",
            package="demo.package",
            actions=(Action("capture_screenshot", {}),),
        )
        with tempfile.TemporaryDirectory() as directory:
            screenshot_directory = Path(directory)
            result = AutomationEngine(
                adb,
                screenshot_directory,
                poll_interval=0,
                sleep_function=lambda _seconds: None,
                screenshot_save_level="key",
            ).run(task)
            files = list(screenshot_directory.glob("capture-demo_screenshot_*.png"))
        self.assertEqual(result.status, RunStatus.SUCCESS)
        self.assertEqual(len(files), 1)
        self.assertEqual(len(result.key_screenshots), 1)

    def test_capture_screenshot_action_skipped_at_none_level(self) -> None:
        adb = FakeAdb()
        task = TaskDefinition(
            id="capture-none-demo",
            name="Capture none demo",
            package="demo.package",
            actions=(Action("capture_screenshot", {"label": "返回小黑盒"}),),
        )
        with tempfile.TemporaryDirectory() as directory:
            screenshot_directory = Path(directory)
            result = AutomationEngine(
                adb,
                screenshot_directory,
                poll_interval=0,
                sleep_function=lambda _seconds: None,
                screenshot_save_level="none",
            ).run(task)
            files = list(screenshot_directory.glob("*.png"))
        self.assertEqual(result.status, RunStatus.SUCCESS)
        self.assertEqual(files, [])

    def test_basic_adb_actions_are_dispatched_with_normalized_arguments(self) -> None:
        adb = FakeAdb()
        engine = AutomationEngine(adb, Path("screenshots"), sleep_function=lambda _seconds: None)

        engine._execute(
            Action("click", {"locate": "coordinate", "x": "12", "y": "34"})
        )
        engine._execute(
            Action(
                "swipe",
                {"x1": "1", "y1": "2", "x2": "3", "y2": "4"},
            )
        )
        engine._execute(Action("back"))

        self.assertEqual(adb.taps, [(12, 34)])
        self.assertEqual(adb.swipes, [(1, 2, 3, 4, 300)])
        self.assertEqual(adb.backs, [1])

    def test_invalid_and_unknown_actions_fail_explicitly(self) -> None:
        engine = AutomationEngine(FakeAdb(), Path("screenshots"))

        with self.assertRaisesRegex(ActionError, "动作缺少参数: result_var"):
            engine._execute(
                Action("detect", {"locate": "ocr", "texts": ["领取"]})
            )
        with self.assertRaisesRegex(ActionError, "不支持的动作类型"):
            engine._execute(Action("removed_action"))

    def test_launch_without_foreground_probe_stops_after_one_launch(self) -> None:
        class NoForegroundProbe:
            serial = "emulator-5556"

            def __init__(self) -> None:
                self.launched: list[str] = []

            def launch(self, package: str) -> None:
                self.launched.append(package)

        adb = NoForegroundProbe()
        engine = AutomationEngine(adb, Path("screenshots"), sleep_function=lambda _seconds: None)

        engine._execute(Action("launch", {"package": "demo.package", "wait_seconds": 0}))

        self.assertEqual(adb.launched, ["demo.package"])

    def test_stop_accepts_optional_package(self) -> None:
        adb = FakeAdb()
        engine = AutomationEngine(adb, Path("screenshots"))

        engine._execute(Action("stop", {"package": "com.other.app"}))

        self.assertEqual(adb.stopped, ["com.other.app"])

    def test_launch_accepts_optional_package_and_wait(self) -> None:
        class LaunchAdb:
            serial = "emulator-5556"

            def __init__(self) -> None:
                self.launched: list[str] = []

            def launch(self, package: str) -> None:
                self.launched.append(package)

        adb = LaunchAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            sleep_function=lambda _seconds: None,
        )

        engine._execute(
            Action(
                "launch",
                {
                    "package": "com.other.app",
                    "wait_seconds": 0,
                    "launch_attempts": 5,
                },
            )
        )

        self.assertEqual(adb.launched, ["com.other.app"])

    def test_detect_ocr_writes_matched_target_to_context(self) -> None:
        engine = AutomationEngine(
            FakeAdb(),
            Path("screenshots"),
            poll_interval=0,
            sleep_function=lambda _seconds: None,
            ocr_client=lambda _image: ["领取"],
        )

        engine._execute(
            Action(
                "detect",
                {
                    "locate": "ocr",
                    "texts": ["已领取", "领取"],
                    "result_var": "ocr_state",
                    "match_mode": "fuzzy",
                    "timeout_seconds": 0.1,
                    "interval_seconds": 0,
                },
            )
        )

        self.assertEqual(engine._context["ocr_state"], "领取")

    def test_detect_ui_writes_matched_target_to_context(self) -> None:
        class DetectUiAdb(FakeAdb):
            def dump_ui(self) -> str:
                return (
                    '<hierarchy>'
                    '<node content-desc="已领取" visible-to-user="true" '
                    'bounds="[0,0][100,80]" />'
                    '</hierarchy>'
                )

        engine = AutomationEngine(
            DetectUiAdb(),
            Path("screenshots"),
            poll_interval=0,
            sleep_function=lambda _seconds: None,
        )

        engine._execute(
            Action(
                "detect",
                {
                    "locate": "ui",
                    "target": "text",
                    "texts": ["已领取", "领取"],
                    "result_var": "ui_state",
                    "timeout_seconds": 0.1,
                    "interval_seconds": 0,
                },
            )
        )

        self.assertEqual(engine._context["ui_state"], "已领取")

    def test_detect_timeout_can_write_none(self) -> None:
        engine = AutomationEngine(
            FakeAdb(),
            Path("screenshots"),
            poll_interval=0,
            sleep_function=lambda _seconds: None,
            ocr_client=lambda _image: ["其他"],
        )

        engine._execute(
            Action(
                "detect",
                {
                    "locate": "ocr",
                    "texts": ["领取"],
                    "result_var": "ocr_state",
                    "timeout_seconds": 0,
                    "interval_seconds": 0,
                    "continue_on_timeout": True,
                },
            )
        )

        self.assertEqual(engine._context["ocr_state"], "")
        self.assertIs(engine._context["ocr_state_found"], False)
        self.assertEqual(engine._context["ocr_state_count"], 0)

    def test_detect_timeout_raises_by_default(self) -> None:
        engine = AutomationEngine(
            FakeAdb(),
            Path("screenshots"),
            poll_interval=0,
            sleep_function=lambda _seconds: None,
            ocr_client=lambda _image: ["其他"],
        )

        with self.assertRaisesRegex(ActionError, "检测超时"):
            engine._execute(
                Action(
                    "detect",
                    {
                        "locate": "ocr",
                        "texts": ["领取"],
                        "result_var": "ocr_state",
                        "timeout_seconds": 0,
                        "interval_seconds": 0,
                    },
                )
            )

    def test_detect_ui_resource_id_writes_result_var(self) -> None:
        class ResourceAdb(FakeAdb):
            def dump_ui(self) -> str:
                return (
                    '<hierarchy>'
                    '<node resource-id="com.example:id/claim" visible-to-user="true" '
                    'bounds="[0,0][100,80]" />'
                    '</hierarchy>'
                )

        engine = AutomationEngine(
            ResourceAdb(),
            Path("screenshots"),
            poll_interval=0,
            sleep_function=lambda _seconds: None,
        )

        engine._execute(
            Action(
                "detect",
                {
                    "locate": "ui",
                    "target": "resource_id",
                    "resource_id": "com.example:id/claim",
                    "result_var": "ui_state",
                    "timeout_seconds": 0.1,
                    "interval_seconds": 0,
                },
            )
        )

        self.assertEqual(engine._context["ui_state"], "com.example:id/claim")

    def test_if_runs_then_or_else_branch(self) -> None:
        adb = FakeAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            poll_interval=0,
            sleep_function=lambda _seconds: None,
        )

        engine._context["state"] = "领取"
        engine._execute(
            Action(
                "if",
                {
                    "var": "state",
                    "equals": "领取",
                    "then": [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}],
                    "else": [{"type": "click", "locate": "coordinate", "x": 3, "y": 4}],
                },
            )
        )
        self.assertEqual(adb.taps, [(1, 2)])

        engine._context["state"] = "其他"
        engine._execute(
            Action(
                "if",
                {
                    "var": "state",
                    "equals": "领取",
                    "then": [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}],
                    "else": [{"type": "click", "locate": "coordinate", "x": 3, "y": 4}],
                },
            )
        )
        self.assertEqual(adb.taps, [(1, 2), (3, 4)])

    def test_click_ui_text_does_not_need_result_var(self) -> None:
        class ClickUiAdb(FakeAdb):
            def dump_ui(self) -> str:
                return (
                    '<hierarchy>'
                    '<node text="签到活动入口" clickable="true" enabled="true" '
                    'visible-to-user="true" bounds="[0,0][200,100]" />'
                    '</hierarchy>'
                )

        adb = ClickUiAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            poll_interval=0,
            sleep_function=lambda _seconds: None,
        )

        engine._execute(
            Action(
                "click",
                {
                    "locate": "ui",
                    "target": "text",
                    "text": "签到%",
                    "match_mode": "fuzzy",
                    "timeout_seconds": 0.1,
                },
            )
        )

        self.assertEqual(adb.taps, [(100, 50)])

    def test_click_ocr_text_does_not_need_result_var(self) -> None:
        adb = FakeAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            poll_interval=0,
            sleep_function=lambda _seconds: None,
            ocr_boxes_client=lambda _image: [
                ("看至第1集", [(100, 200), (300, 200), (300, 300), (100, 300)])
            ],
        )

        engine._execute(
            Action(
                "click",
                {
                    "locate": "ocr",
                    "text": "看至第_集",
                    "match_mode": "fuzzy",
                    "timeout_seconds": 0.1,
                },
            )
        )

        self.assertEqual(adb.taps, [(200, 250)])

    def test_click_ui_skips_when_skip_text_present(self) -> None:
        class SkipClickAdb(FakeAdb):
            def dump_ui(self) -> str:
                return (
                    '<hierarchy>'
                    '<node content-desc="已签到" visible-to-user="true" '
                    'bounds="[0,0][100,80]" />'
                    '</hierarchy>'
                )

        adb = SkipClickAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            poll_interval=0,
            sleep_function=lambda _seconds: None,
        )

        engine._execute(
            Action(
                "click",
                {
                    "locate": "ui",
                    "target": "text",
                    "text": "签到",
                    "skip_if_texts": ["已签到"],
                    "timeout_seconds": 0.1,
                },
            )
        )

        self.assertEqual(adb.taps, [])

    def test_skip_detect_and_if_skips_coordinate_click(self) -> None:
        class SkipAdb(FakeAdb):
            def dump_ui(self) -> str:
                return (
                    '<hierarchy>'
                    '<node content-desc="已签到" visible-to-user="true" '
                    'bounds="[0,0][100,80]" />'
                    '</hierarchy>'
                )

        adb = SkipAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            poll_interval=0,
            sleep_function=lambda _seconds: None,
        )
        engine._execute(
            Action(
                "detect",
                {
                    "locate": "ui",
                    "target": "text",
                    "texts": ["已签到"],
                    "result_var": "skip_state",
                    "timeout_seconds": 0.1,
                    "interval_seconds": 0,
                },
            )
        )
        engine._execute(
            Action(
                "if",
                {
                    "var": "skip_state_found",
                    "equals": False,
                    "then": [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}],
                },
            )
        )
        self.assertEqual(adb.taps, [])

    def test_failure_saves_screenshot(self) -> None:
        adb = FakeAdb()
        task = TaskDefinition(
            id="demo",
            name="Demo",
            package="demo.package",
            actions=(Action("unknown_action"),),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = AutomationEngine(
                adb, Path(directory), poll_interval=0, sleep_function=lambda _seconds: None
            ).run(task)
            self.assertEqual(result.status, RunStatus.FAILED)
            self.assertIsNotNone(result.screenshot)
            self.assertTrue(result.screenshot.exists())

    def test_key_level_captures_screenshot_actions_only(self) -> None:
        adb = FakeAdb()
        task = TaskDefinition(
            id="key-demo",
            name="Key demo",
            package="demo.package",
            actions=(Action("capture_screenshot", {}),),
        )
        with tempfile.TemporaryDirectory() as directory:
            screenshot_directory = Path(directory)
            result = AutomationEngine(
                adb,
                screenshot_directory,
                poll_interval=0,
                sleep_function=lambda _seconds: None,
                screenshot_save_level="key",
            ).run(task)
            step_files = list(screenshot_directory.glob("key-demo_step_01_*.png"))
            key_files = list(screenshot_directory.glob("key-demo_screenshot_*.png"))

        self.assertEqual(result.status, RunStatus.SUCCESS)
        self.assertEqual(step_files, [])
        self.assertEqual(len(key_files), 1)
        self.assertEqual(len(result.key_screenshots), 1)

    def test_none_level_saves_no_action_or_key_screenshots(self) -> None:
        adb = FakeAdb()
        task = TaskDefinition(
            id="none-demo",
            name="None demo",
            package="demo.package",
            actions=(Action("capture_screenshot", {"label": "已签到"}),),
        )
        with tempfile.TemporaryDirectory() as directory:
            screenshot_directory = Path(directory)
            result = AutomationEngine(
                adb,
                screenshot_directory,
                poll_interval=0,
                sleep_function=lambda _seconds: None,
                screenshot_save_level="none",
            ).run(task)
            files = list(screenshot_directory.glob("*.png"))

        self.assertEqual(result.status, RunStatus.SUCCESS)
        self.assertEqual(files, [])
        self.assertEqual(result.key_screenshots, ())

    def test_none_level_skips_failure_screenshot_too(self) -> None:
        adb = FakeAdb()
        task = TaskDefinition(
            id="none-failure-demo",
            name="None failure demo",
            package="demo.package",
            actions=(Action("unknown_action"),),
        )
        with tempfile.TemporaryDirectory() as directory:
            screenshot_directory = Path(directory)
            result = AutomationEngine(
                adb,
                screenshot_directory,
                poll_interval=0,
                sleep_function=lambda _seconds: None,
                screenshot_save_level="none",
            ).run(task)
            files = list(screenshot_directory.glob("*.png"))

        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertIsNone(result.screenshot)
        self.assertEqual(files, [])

    def test_all_level_keeps_checkpoints_and_key_pages(self) -> None:
        adb = FakeAdb()
        logs: list[str] = []
        task = TaskDefinition(
            id="all-demo",
            name="All demo",
            package="demo.package",
            actions=(Action("capture_screenshot", {}),),
        )
        with tempfile.TemporaryDirectory() as directory:
            screenshot_directory = Path(directory)
            result = AutomationEngine(
                adb,
                screenshot_directory,
                poll_interval=0,
                sleep_function=lambda _seconds: None,
                screenshot_save_level="all",
                log_callback=logs.append,
            ).run(task)
            step_files = list(screenshot_directory.glob("all-demo_step_01_*.png"))
            key_files = list(screenshot_directory.glob("all-demo_screenshot_*.png"))

        self.assertEqual(result.status, RunStatus.SUCCESS)
        self.assertEqual(len(step_files), 2)
        self.assertEqual(len(key_files), 1)
        self.assertEqual(len(result.key_screenshots), 1)
        joined_logs = "\n".join(logs)
        self.assertIn("截图[before]", joined_logs)
        self.assertIn("截图[after]", joined_logs)
        self.assertIn("关键页面截图:", joined_logs)

    def test_lifecycle_actions_use_task_level_package_and_wait(self) -> None:
        adb = FakeAdb()
        sleeps: list[float] = []
        logs: list[str] = []
        clock = [0.0]

        def monotonic() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock[0] += seconds

        task = TaskDefinition(
            id="lifecycle-demo",
            name="Lifecycle demo",
            package="tv.danmaku.bili",
            actions=(Action("stop"), Action("launch", {"wait_seconds": 7.5})),
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch("free_app.engine.time.monotonic", side_effect=monotonic):
                result = AutomationEngine(
                    adb,
                    Path(directory),
                    log_callback=logs.append,
                    poll_interval=0,
                    sleep_function=sleep,
                ).run(task)

        self.assertEqual(result.status, RunStatus.SUCCESS)
        self.assertEqual(adb.stopped, ["tv.danmaku.bili"])
        self.assertEqual(adb.launched, ["tv.danmaku.bili"])
        self.assertAlmostEqual(sum(sleeps), 7.5, places=6)
        self.assertTrue(any("等待 7.5s" in message for message in logs))
        self.assertTrue(any('"package":"tv.danmaku.bili"' in message for message in logs))
        self.assertTrue(any('"wait_seconds":7.5' in message for message in logs))

    def test_effective_parameters_include_implicit_defaults(self) -> None:
        from free_app.action_schema import effective_parameters

        self.assertEqual(
            effective_parameters("wait", {}),
            {"seconds": 1, "retries": 0},
        )
        self.assertEqual(
            effective_parameters("launch", {}),
            {"launch_attempts": 3, "retries": 0},
        )
        self.assertEqual(
            effective_parameters("swipe", {"x1": 1, "y1": 2, "x2": 3, "y2": 4}),
            {
                "x1": 1,
                "y1": 2,
                "x2": 3,
                "y2": 4,
                "duration_ms": 300,
                "retries": 0,
            },
        )

    def test_stop_requested_before_run_is_not_cleared(self) -> None:
        adb = FakeAdb()
        task = TaskDefinition(
            id="stop-demo",
            name="Stop demo",
            package="demo.package",
            actions=(Action("unknown_action"),),
        )
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            poll_interval=0,
            sleep_function=lambda _seconds: None,
        )
        engine.request_stop()

        result = engine.run(task)

        self.assertEqual(result.status, RunStatus.STOPPED)
        self.assertEqual(adb.taps, [])

    def test_launch_retries_until_target_app_is_in_foreground(self) -> None:
        class LaunchAdb:
            serial = "emulator-5556"

            def __init__(self) -> None:
                self.launched: list[str] = []

            def launch(self, package: str) -> None:
                self.launched.append(package)

            def current_package(self) -> str:
                return "tv.danmaku.bili" if len(self.launched) >= 2 else "app.lawnchair"

        adb = LaunchAdb()
        logs: list[str] = []
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            log_callback=logs.append,
            poll_interval=0,
            sleep_function=lambda _seconds: None,
        )

        engine._execute(
            Action(
                "launch",
                {
                    "package": "tv.danmaku.bili",
                    "wait_seconds": 0,
                    "launch_attempts": 3,
                },
            )
        )

        self.assertEqual(adb.launched, ["tv.danmaku.bili", "tv.danmaku.bili"])
        self.assertTrue(any("前台为 app.lawnchair，将重试" in message for message in logs))

    def test_launch_continues_with_warning_when_foreground_never_changes(self) -> None:
        class StuckAdb:
            serial = "emulator-5556"

            def __init__(self) -> None:
                self.launched: list[str] = []

            def launch(self, package: str) -> None:
                self.launched.append(package)

            def current_package(self) -> str:
                return "app.lawnchair"

        adb = StuckAdb()
        logs: list[str] = []
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            log_callback=logs.append,
            poll_interval=0,
            sleep_function=lambda _seconds: None,
        )

        engine._execute(
            Action(
                "launch",
                {
                    "package": "tv.danmaku.bili",
                    "wait_seconds": 0,
                    "launch_attempts": 2,
                },
            )
        )

        self.assertEqual(adb.launched, ["tv.danmaku.bili", "tv.danmaku.bili"])
        self.assertTrue(any("警告: 启动 2 次后前台仍为 app.lawnchair" in message for message in logs))

    def test_invalid_screenshot_level_falls_back_to_all(self) -> None:
        engine = AutomationEngine(
            FakeAdb(),
            Path("screenshots"),
            screenshot_save_level="bad",
        )

        self.assertEqual(engine.screenshot_save_level, "all")

    def test_run_with_retries_sleeps_between_failed_attempts(self) -> None:
        sleeps: list[float] = []
        clock = [0.0]

        def monotonic() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock[0] += seconds

        engine = AutomationEngine(
            FakeAdb(),
            Path("screenshots"),
            sleep_function=sleep,
        )

        with patch("free_app.engine.time.monotonic", side_effect=monotonic):
            with self.assertRaisesRegex(ActionError, "不支持的动作类型"):
                engine._run_with_retries(Action("unknown_action", {"retries": 2}))

        self.assertAlmostEqual(sum(sleeps), 2, places=6)

    def test_back_presses_once(self) -> None:
        adb = FakeAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            sleep_function=lambda _seconds: None,
        )

        engine._execute(Action("back"))
        self.assertEqual(adb.backs, [1])

    def test_swipe_rejects_negative_duration(self) -> None:
        engine = AutomationEngine(FakeAdb(), Path("screenshots"))

        with self.assertRaisesRegex(ActionError, "non-negative"):
            engine._execute(
                Action(
                    "swipe",
                    {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "duration_ms": -1},
                )
            )

    def test_launch_stops_after_foreground_probe_failure(self) -> None:
        class ProbeFailureAdb(FakeAdb):
            def __init__(self) -> None:
                super().__init__()
                self.launched: list[str] = []

            def launch(self, package: str) -> None:
                self.launched.append(package)

            def current_package(self) -> str:
                raise RuntimeError("probe down")

        adb = ProbeFailureAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            sleep_function=lambda _seconds: None,
        )

        engine._execute(
            Action(
                "launch",
                {"package": "demo.package", "wait_seconds": 0, "launch_attempts": 3},
            )
        )

        self.assertEqual(adb.launched, ["demo.package"])

    def test_detect_ocr_boxes_writes_count_and_coordinate(self) -> None:
        engine = AutomationEngine(
            FakeAdb(),
            Path("screenshots"),
            sleep_function=lambda _seconds: None,
            ocr_boxes_client=lambda _image: [
                ("领取", [(100, 200), (300, 200), (300, 300), (100, 300)]),
                ("已领取", [(100, 400), (300, 400), (300, 500), (100, 500)]),
            ],
        )

        engine._execute(
            Action(
                "detect",
                {
                    "locate": "ocr",
                    "texts": ["领取", "已领取"],
                    "result_var": "state",
                    "timeout_seconds": 0.1,
                    "interval_seconds": 0,
                },
            )
        )

        self.assertEqual(engine._context["state"], "领取")
        self.assertEqual(engine._context["state_count"], 2)
        self.assertEqual(engine._context["state_coord"], (200, 250))

    def test_detect_ui_resource_id_writes_count(self) -> None:
        class MultipleResourceAdb(FakeAdb):
            def dump_ui(self) -> str:
                return (
                    "<hierarchy>"
                    '<node resource-id="com.demo:id/claim" visible-to-user="true" '
                    'bounds="[0,0][100,80]" />'
                    '<node resource-id="com.demo:id/claim" visible-to-user="true" '
                    'bounds="[0,100][100,180]" />'
                    "</hierarchy>"
                )

        engine = AutomationEngine(
            MultipleResourceAdb(),
            Path("screenshots"),
            sleep_function=lambda _seconds: None,
        )

        engine._execute(
            Action(
                "detect",
                {
                    "locate": "ui",
                    "target": "resource_id",
                    "resource_id": "com.demo:id/claim",
                    "result_var": "state",
                    "timeout_seconds": 0.1,
                    "interval_seconds": 0,
                },
            )
        )

        self.assertEqual(engine._context["state_count"], 2)
        self.assertEqual(engine._context["state_coord"], (50, 40))

    def test_click_coordinate_taps_clamped_coordinates(self) -> None:
        adb = FakeAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            sleep_function=lambda _seconds: None,
        )

        engine._execute(Action("click", {"locate": "coordinate", "x": -5, "y": 99999}))

        self.assertEqual(adb.taps, [(0, SCREEN_HEIGHT - 1)])

    def test_click_ocr_skips_when_skip_text_is_present(self) -> None:
        adb = FakeAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            sleep_function=lambda _seconds: None,
            ocr_boxes_client=lambda _image: [
                ("已领取", [(100, 200), (300, 200), (300, 300), (100, 300)])
            ],
        )

        engine._execute(
            Action(
                "click",
                {
                    "locate": "ocr",
                    "text": "领取",
                    "skip_if_texts": ["已领取"],
                    "timeout_seconds": 0.1,
                    "interval_seconds": 0,
                },
            )
        )

        self.assertEqual(adb.taps, [])

    def test_click_ui_disabled_node_raises_after_timeout(self) -> None:
        class DisabledAdb(FakeAdb):
            def dump_ui(self) -> str:
                return (
                    '<hierarchy><node text="领取" enabled="false" clickable="true" '
                    'visible-to-user="true" bounds="[0,0][100,80]" /></hierarchy>'
                )

        engine = AutomationEngine(
            DisabledAdb(),
            Path("screenshots"),
            sleep_function=lambda _seconds: None,
        )

        with self.assertRaises(ActionError):
            engine._execute(
                Action(
                    "click",
                    {
                        "locate": "ui",
                        "target": "text",
                        "text": "领取",
                        "timeout_seconds": 0,
                        "interval_seconds": 0,
                    },
                )
            )

    def test_run_nested_steps_rejects_bad_step_containers(self) -> None:
        engine = AutomationEngine(FakeAdb(), Path("screenshots"))

        with self.assertRaisesRegex(ActionError, "必须是动作列表"):
            engine._run_nested_steps({}, "demo")
        with self.assertRaisesRegex(ActionError, "必须是动作对象"):
            engine._run_nested_steps([123], "demo")
        with self.assertRaisesRegex(ActionError, "不支持复合动作"):
            engine._run_nested_steps([{"type": "compound", "name": "demo"}], "demo")

    def test_string_values_and_match_mode_helpers(self) -> None:
        engine = AutomationEngine(FakeAdb(), Path("screenshots"))

        self.assertEqual(engine._string_values(" 领取 "), ["领取"])
        self.assertEqual(engine._string_values(None), [])
        with self.assertRaisesRegex(ActionError, "不支持的文本匹配方式"):
            engine._text_match_mode("regex")

    def test_ocr_boxes_normalizes_malformed_results(self) -> None:
        engine = AutomationEngine(
            FakeAdb(),
            Path("screenshots"),
            ocr_boxes_client=lambda _image: [
                "bad",
                ("", []),
                ("领取", [(10, 20), ("bad", 30)]),
                ("其他", [(1, 2), (3, 4), (5, 6)]),
            ],
        )

        boxes = engine._ocr_boxes()

        self.assertEqual([text for text, _points in boxes], ["领取", "其他"])
        self.assertEqual(boxes[0][1], [(10, 20)])

    def test_ocr_client_requires_a_client(self) -> None:
        engine = AutomationEngine(FakeAdb(), Path("screenshots"))

        with self.assertRaisesRegex(ActionError, "未配置 OCR 客户端"):
            engine._ocr_boxes()

    def test_log_current_package_handles_probe_failure(self) -> None:
        class BrokenProbeAdb(FakeAdb):
            def current_package(self) -> str:
                raise RuntimeError("probe down")

        logs: list[str] = []
        engine = AutomationEngine(
            BrokenProbeAdb(),
            Path("screenshots"),
            log_callback=logs.append,
        )

        engine._log_current_package("动作前台")

        self.assertTrue(any("读取失败" in message for message in logs))

    def test_capture_checkpoint_and_key_screenshot_ignore_screenshot_failures(self) -> None:
        class BrokenScreenshotAdb(FakeAdb):
            def screenshot(self) -> bytes:
                raise OSError("screenshot failed")

        logs: list[str] = []
        engine = AutomationEngine(
            BrokenScreenshotAdb(),
            Path("screenshots"),
            log_callback=logs.append,
            screenshot_save_level="all",
        )

        engine._capture_checkpoint("demo", 1, "before")
        engine._capture_key_screenshot("demo", "成功")
        self.assertIsNone(engine._save_failure_screenshot("demo", 1))

        self.assertTrue(any("失败" in message for message in logs))

    def test_run_reports_progress_callback(self) -> None:
        progress: list[tuple[int, int, str]] = []
        engine = AutomationEngine(
            FakeAdb(),
            Path("screenshots"),
            screenshot_save_level="key",
            progress_callback=lambda index, total, description: progress.append(
                (index, total, description)
            ),
        )
        task = TaskDefinition(
            id="progress-demo",
            name="Progress demo",
            package="demo.package",
            actions=(Action("wait", {"seconds": 0}),),
        )

        result = engine.run(task)

        self.assertEqual(result.status, RunStatus.SUCCESS)
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0][0:2], (1, 1))

    def test_run_with_retries_reraise_stop_requested(self) -> None:
        engine = AutomationEngine(
            FakeAdb(),
            Path("screenshots"),
            sleep_function=lambda _seconds: engine.request_stop(),
        )

        with self.assertRaises(StopRequested):
            engine._run_with_retries(Action("unknown_action", {"retries": 2}))

    def test_detect_rejects_bad_locate_and_missing_targets(self) -> None:
        engine = AutomationEngine(FakeAdb(), Path("screenshots"))

        with self.assertRaisesRegex(ActionError, "不支持的检测来源"):
            engine._execute(
                Action(
                    "detect",
                    {"locate": "bad", "texts": ["领取"], "result_var": "state"},
                )
            )
        with self.assertRaisesRegex(ActionError, "缺少 texts"):
            engine._execute(Action("detect", {"locate": "ocr", "result_var": "state"}))

    def test_if_handles_string_boolean_and_missing_equals(self) -> None:
        adb = FakeAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            sleep_function=lambda _seconds: None,
        )
        engine._context["state"] = True
        engine._execute(
            Action(
                "if",
                {
                    "var": "state",
                    "equals": "false",
                    "then": [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}],
                    "else": [{"type": "click", "locate": "coordinate", "x": 3, "y": 4}],
                },
            )
        )
        self.assertEqual(adb.taps, [(3, 4)])

        with self.assertRaisesRegex(ActionError, "缺少有效的 equals"):
            engine._execute(Action("if", {"var": "state"}))

    def test_click_rejects_invalid_locate(self) -> None:
        adb = FakeAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            sleep_function=lambda _seconds: None,
        )
        with self.assertRaisesRegex(ActionError, "locate 必须是 ui/ocr/coordinate"):
            engine._execute(
                Action(
                    "click",
                    {
                        "locate": "bad",
                        "text": "领取",
                        "timeout_seconds": 0.1,
                    },
                )
            )
        self.assertEqual(adb.taps, [])

    def test_run_nested_steps_reports_invalid_action_dict(self) -> None:
        engine = AutomationEngine(FakeAdb(), Path("screenshots"))

        with self.assertRaisesRegex(ActionError, "动作无效"):
            engine._run_nested_steps([{"text": "missing type"}], "demo")

    def test_loop_until_handles_string_boolean_and_immediate_match(self) -> None:
        adb = FakeAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            sleep_function=lambda _seconds: None,
        )
        engine._context["state"] = False
        engine._execute(
            Action(
                "loop_until",
                {
                    "var": "state",
                    "equals": "false",
                    "max_iterations": 3,
                    "steps": [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}],
                },
            )
        )
        self.assertEqual(adb.taps, [])

    def test_lifecycle_uses_task_package_and_rejects_bad_wait(self) -> None:
        adb = FakeAdb()
        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            sleep_function=lambda _seconds: None,
        )
        engine._current_task = TaskDefinition(
            id="demo",
            name="Demo",
            package="demo.package",
            actions=(),
        )

        engine._execute(Action("stop"))
        self.assertEqual(adb.stopped, ["demo.package"])

        with self.assertRaisesRegex(ActionError, "wait_seconds"):
            engine._execute(Action("launch", {"wait_seconds": "bad"}))

    def test_log_screen_info_rejects_wrong_resolution(self) -> None:
        class WrongScreenAdb(FakeAdb):
            def screen_info(self) -> object:
                from free_app.adb import ScreenInfo

                return ScreenInfo(100, 100, 100)

        engine = AutomationEngine(WrongScreenAdb(), Path("screenshots"))

        with self.assertRaisesRegex(ActionError, "模拟器规格"):
            engine._log_screen_info()

    def test_log_current_package_uses_unknown_when_probe_returns_none(self) -> None:
        class NoneProbeAdb(FakeAdb):
            def current_package(self) -> str | None:
                return None

        logs: list[str] = []
        engine = AutomationEngine(
            NoneProbeAdb(),
            Path("screenshots"),
            log_callback=logs.append,
        )

        engine._log_current_package("动作前台")

        self.assertTrue(any("未知" in message for message in logs))

    def test_ocr_claim_or_watch_compound_skips_direct_claim(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        library = load_action_library(project_root / "config" / "actions")
        actions, error = expand_action_for_run(
            {"type": "compound", "name": "ocr_claim_or_watch"},
            library,
        )
        self.assertIsNone(error)

        task = TaskDefinition(
            id="ocr-claim-demo",
            name="OCR claim demo",
            package="tv.danmaku.bili",
            actions=tuple(actions),
        )
        adb = FakeAdb()
        with tempfile.TemporaryDirectory() as directory:
            result = AutomationEngine(
                adb,
                Path(directory),
                sleep_function=lambda _seconds: None,
                screenshot_save_level="none",
                ocr_boxes_client=lambda _image: [
                    ("领取", [(100, 200), (300, 200), (300, 300), (100, 300)])
                ],
            ).run(task)

        self.assertEqual(result.status, RunStatus.SUCCESS)
        self.assertEqual(adb.taps, [])

    def test_ocr_claim_or_watch_compound_claims_after_watching(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        library = load_action_library(project_root / "config" / "actions")
        actions, error = expand_action_for_run(
            {"type": "compound", "name": "ocr_claim_or_watch"},
            library,
        )
        self.assertIsNone(error)

        task = TaskDefinition(
            id="ocr-watch-demo",
            name="OCR watch demo",
            package="tv.danmaku.bili",
            actions=tuple(actions),
        )
        adb = FakeAdb()
        box = [(100, 200), (300, 200), (300, 300), (100, 300)]
        calls = 0

        def stateful_ocr(_image: bytes) -> list[tuple[str, list[tuple[int, int]]]]:
            nonlocal calls
            results = [
                [("去观看", box)],
                [("去观看", box)],
                [("看至第3集", box)],
                [("领取", box)],
                [("领取", box)],
            ]
            result = results[min(calls, len(results) - 1)]
            calls += 1
            return result

        clock = [0.0]

        def monotonic() -> float:
            return clock[0]

        def advance_clock(seconds: float) -> None:
            clock[0] += seconds

        with patch("free_app.engine.time.monotonic", side_effect=monotonic):
            with tempfile.TemporaryDirectory() as directory:
                result = AutomationEngine(
                    adb,
                    Path(directory),
                    sleep_function=advance_clock,
                    screenshot_save_level="none",
                    ocr_boxes_client=stateful_ocr,
                ).run(task)

        self.assertEqual(result.status, RunStatus.SUCCESS)
        self.assertEqual(adb.taps, [(200, 250), (200, 250), (200, 250)])
        self.assertEqual(adb.backs, [1, 1])

    def test_action_retry_reconnects_adb_before_next_attempt(self) -> None:
        class FlakyAdb(FakeAdb):
            def __init__(self) -> None:
                super().__init__()
                self.fail_force_stop = True
                self.reconnect_count = 0

            def force_stop(self, package: str) -> None:
                if self.fail_force_stop:
                    self.fail_force_stop = False
                    raise AdbError("device offline")
                self.stopped.append(package)

            def reconnect(self) -> bool:
                self.reconnect_count += 1
                return True

        adb = FlakyAdb()
        clock = [0.0]

        def monotonic() -> float:
            return clock[0]

        def advance_clock(seconds: float) -> None:
            clock[0] += seconds

        engine = AutomationEngine(
            adb,
            Path("screenshots"),
            sleep_function=advance_clock,
        )

        with patch("free_app.engine.time.monotonic", side_effect=monotonic):
            engine._run_with_retries(
                Action("stop", {"package": "demo.package", "retries": 1})
            )

        self.assertEqual(adb.stopped, ["demo.package"])
        self.assertEqual(adb.reconnect_count, 1)

    def test_all_shipped_tasks_run_successfully_in_fake_environment(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        tasks, errors = load_task_directory(
            project_root / "config" / "tasks",
            {"qq_group_name": "测试群"},
        )
        self.assertEqual(errors, [])

        full_ui_xml = """
        <hierarchy>
          <node text="更多" enabled="true" clickable="true" visible-to-user="true"
                bounds="[100,100][200,150]" />
          <node text="我的" enabled="true" clickable="true" visible-to-user="true"
                bounds="[100,100][200,150]" />
          <node text="大会员中心" enabled="true" clickable="true" visible-to-user="true"
                bounds="[100,100][200,150]" />
          <node text="签到" enabled="true" clickable="true" visible-to-user="true"
                bounds="[100,100][200,150]" />
          <node text="已签到" enabled="true" clickable="true" visible-to-user="true"
                bounds="[100,100][200,150]" />
          <node text="视频," enabled="true" clickable="true" visible-to-user="true"
                bounds="[100,100][200,150]" />
          <node text="QQ" enabled="true" clickable="true" visible-to-user="true"
                bounds="[100,100][200,150]" />
          <node text="发送" enabled="true" clickable="true" visible-to-user="true"
                bounds="[100,100][200,150]" />
          <node text="测试群" enabled="true" clickable="true" visible-to-user="true"
                bounds="[100,100][200,150]" />
          <node resource-id="tv.danmaku.bili:id/frame_share" enabled="true"
                clickable="true" visible-to-user="true" bounds="[100,100][200,150]" />
          <node resource-id="com.max.xiaoheihe:id/epoxy_model_group_child_container"
                enabled="true" clickable="true" visible-to-user="true"
                bounds="[100,100][200,150]" />
          <node resource-id="com.max.xiaoheihe:id/iv_appbar_action_button"
                enabled="true" clickable="true" visible-to-user="true"
                bounds="[100,100][200,150]" />
          <node resource-id="com.tencent.mobileqq:id/dialogLeftBtn" enabled="true"
                clickable="true" visible-to-user="true" bounds="[100,100][200,150]" />
        </hierarchy>
        """

        class FullUiAdb(FakeAdb):
            def dump_ui(self) -> str:
                return full_ui_xml

        adb = FullUiAdb()
        box = [(100, 100), (200, 100), (200, 150), (100, 150)]

        def ocr(_image: bytes) -> list[tuple[str, list[tuple[int, int]]]]:
            return [
                ("已领取", box),
                ("领取", box),
                ("签到", box),
                ("去观看", box),
                ("看至第3集", box),
            ]

        clock = [0.0]

        def monotonic() -> float:
            return clock[0]

        def advance_clock(seconds: float) -> None:
            clock[0] += seconds

        with patch("free_app.engine.time.monotonic", side_effect=monotonic):
            with tempfile.TemporaryDirectory() as directory:
                engine = AutomationEngine(
                    adb,
                    Path(directory),
                    sleep_function=advance_clock,
                    screenshot_save_level="none",
                    ocr_boxes_client=ocr,
                )
                for task in tasks:
                    with self.subTest(task_id=task.id):
                        result = engine.run(task)
                        self.assertEqual(
                            result.status,
                            RunStatus.SUCCESS,
                            result.error or result.failed_step,
                        )
