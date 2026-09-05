from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from free_app.app_lifecycle import cleanup_apps, collect_packages
from free_app.config import load_settings


class FakeAdb:
    def __init__(self) -> None:
        self.stopped: list[str] = []

    def force_stop(self, package: str) -> None:
        self.stopped.append(package)


def _tasks(*packages: str) -> list[SimpleNamespace]:
    """构建任务桩：每个包名一个，只设置任务级 ``package``（无动作）。"""
    return [SimpleNamespace(package=package, actions=[]) for package in packages]


def load_sanitized(payload: dict[str, Any]) -> dict[str, Any]:
    """把 ``payload`` 写入临时文件，返回完整清洗后的设置。"""

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "settings.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_settings(path)


class AppLifecycleTests(unittest.TestCase):
    def test_cleanup_stops_task_packages_once_and_logs(self) -> None:
        adb = FakeAdb()
        logs: list[str] = []
        sleeps: list[float] = []
        cleanup_apps(
            adb,
            load_sanitized({"cleanup_after_task": True}),
            _tasks("com.tencent.mobileqq", "com.tencent.mobileqq", "com.hanser.club"),
            logs.append,
            sleep_function=sleeps.append,
        )

        self.assertEqual(adb.stopped, ["com.tencent.mobileqq", "com.hanser.club"])
        self.assertEqual(sleeps, [5.0])
        self.assertTrue(any("5 秒后开始关闭 App 进程" in message for message in logs))
        self.assertTrue(any("开始关闭 App 进程" in message for message in logs))

    def test_cleanup_collects_action_and_nested_packages(self) -> None:
        task = SimpleNamespace(
            package="com.task",
            actions=[
                {"type": "launch", "package": "com.launched"},
                {
                    "type": "if",
                    "then": [{"type": "stop", "package": "com.nested"}],
                    "else": [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}],
                },
                {
                    "type": "compound",
                    "name": "demo",
                    "steps": [{"type": "launch", "package": "com.in_compound"}],
                },
            ],
        )

        self.assertEqual(
            collect_packages([task]),
            ["com.task", "com.launched", "com.nested", "com.in_compound"],
        )

    def test_cleanup_can_be_disabled(self) -> None:
        adb = FakeAdb()
        cleanup_apps(
            adb,
            load_sanitized({"cleanup_after_task": False}),
            _tasks("com.hanser.club"),
        )
        self.assertEqual(adb.stopped, [])

    def test_cleanup_after_task_none_is_normalized_to_enabled_by_sanitizer(
        self,
    ) -> None:
        # 旧语义（cleanup_apps 内 None/"" 视为启用）已删除；
        # 现在由 load_settings 清洗层把 None/""/缺失统一规范为 bool True。
        for payload in (
            {},
            {"cleanup_after_task": None},
            {"cleanup_after_task": ""},
        ):
            with self.subTest(payload=payload):
                settings = load_sanitized(payload)
                self.assertIs(settings["cleanup_after_task"], True)
                adb = FakeAdb()
                sleeps: list[float] = []
                cleanup_apps(
                    adb,
                    settings,
                    _tasks("com.hanser.club"),
                    sleep_function=sleeps.append,
                )
                self.assertEqual(adb.stopped, ["com.hanser.club"])
                self.assertEqual(sleeps, [5.0])

    def test_cleanup_skips_when_no_packages_configured(self) -> None:
        adb = FakeAdb()
        logs: list[str] = []
        cleanup_apps(
            adb,
            load_sanitized({}),
            [],
            logs.append,
            sleep_function=lambda _seconds: None,
        )

        self.assertEqual(adb.stopped, [])
        self.assertTrue(logs)
        self.assertTrue(any("任务里没有配置包名" in message for message in logs))

    def test_cleanup_logs_force_stop_failure_and_continues(self) -> None:
        class FailingAdb(FakeAdb):
            def force_stop(self, package: str) -> None:
                self.stopped.append(package)
                if package == "com.bad":
                    raise RuntimeError("ADB stopped responding")

        adb = FailingAdb()
        logs: list[str] = []
        cleanup_apps(
            adb,
            load_sanitized({"cleanup_delay_seconds": -1}),
            _tasks("com.bad", "com.good"),
            logs.append,
            sleep_function=lambda _seconds: None,
        )

        self.assertEqual(adb.stopped, ["com.bad", "com.good"])
        self.assertTrue(any("ADB stopped responding" in message for message in logs))

    def test_cleanup_rejects_dirty_delay_types_instead_of_silently_falling_back(
        self,
    ) -> None:
        # helpers.number_setting 已严格化：绕过清洗层直接喂脏类型会抛错，
        # 不再静默回退到默认值。
        cases = [
            ("5", TypeError),
            (None, TypeError),
            (True, TypeError),
            (float("nan"), ValueError),
            (float("inf"), ValueError),
        ]
        for delay_seconds, expected_error in cases:
            with self.subTest(delay_seconds=delay_seconds):
                with self.assertRaises(expected_error):
                    cleanup_apps(
                        FakeAdb(),
                        {
                            "cleanup_after_task": True,
                            "cleanup_delay_seconds": delay_seconds,
                        },
                        _tasks("com.hanser.club"),
                        sleep_function=lambda _seconds: None,
                    )


if __name__ == "__main__":
    unittest.main()
