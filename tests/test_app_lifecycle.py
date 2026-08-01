from __future__ import annotations

import unittest

from free_app.app_lifecycle import cleanup_apps


class FakeAdb:
    def __init__(self) -> None:
        self.stopped: list[str] = []

    def force_stop(self, package: str) -> None:
        self.stopped.append(package)


class AppLifecycleTests(unittest.TestCase):
    def test_cleanup_stops_configured_apps_once_and_logs(self) -> None:
        adb = FakeAdb()
        logs: list[str] = []
        sleeps: list[float] = []
        cleanup_apps(
            adb,
            {
                "cleanup_after_task": True,
                "cleanup_packages": ["com.tencent.mobileqq", "com.tencent.mobileqq", "com.hanser.club"],
            },
            logs.append,
            sleep_function=sleeps.append,
        )

        self.assertEqual(adb.stopped, ["com.tencent.mobileqq", "com.hanser.club"])
        self.assertEqual(sleeps, [3.0])
        self.assertTrue(any("3 秒后开始关闭 App 进程" in message for message in logs))
        self.assertTrue(any("开始关闭 App 进程" in message for message in logs))

    def test_cleanup_can_be_disabled(self) -> None:
        adb = FakeAdb()
        cleanup_apps(
            adb,
            {"cleanup_after_task": False, "cleanup_packages": ["com.hanser.club"]},
        )
        self.assertEqual(adb.stopped, [])

    def test_cleanup_defaults_to_enabled_when_field_is_missing_or_none(self) -> None:
        for settings in (
            {"cleanup_packages": ["com.hanser.club"]},
            {"cleanup_after_task": None, "cleanup_packages": ["com.hanser.club"]},
        ):
            with self.subTest(settings=settings):
                adb = FakeAdb()
                sleeps: list[float] = []
                cleanup_apps(
                    adb,
                    settings,
                    sleep_function=sleeps.append,
                )
                self.assertEqual(adb.stopped, ["com.hanser.club"])
                self.assertEqual(sleeps, [3.0])

    def test_cleanup_skips_invalid_package_configuration(self) -> None:
        for settings in (
            {"cleanup_packages": "com.hanser.club"},
            {"cleanup_packages": ["", "  "]},
        ):
            with self.subTest(settings=settings):
                adb = FakeAdb()
                logs: list[str] = []
                cleanup_apps(adb, settings, logs.append, sleep_function=lambda _seconds: None)

                self.assertEqual(adb.stopped, [])
                self.assertTrue(logs)

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
            {"cleanup_packages": ["com.bad", "com.good"], "cleanup_delay_seconds": -1},
            logs.append,
            sleep_function=lambda _seconds: None,
        )

        self.assertEqual(adb.stopped, ["com.bad", "com.good"])
        self.assertTrue(any("ADB stopped responding" in message for message in logs))


if __name__ == "__main__":
    unittest.main()
