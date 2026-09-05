from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from free_app.adb import AdbClient, AdbError, Device


class StubAdb(AdbClient):
    def __init__(self, output: str):
        super().__init__(Path("C:/adb.exe"))
        self.output = output

    def _run(self, arguments, timeout=None, check=True):  # type: ignore[no-untyped-def]
        return self.output


class DumpStub(AdbClient):
    def __init__(self) -> None:
        super().__init__(Path("C:/adb.exe"))
        self.calls: list[tuple[tuple[str, ...], bool]] = []

    def shell(self, *arguments: str, check: bool = True) -> str:
        self.calls.append((arguments, check))
        return '<hierarchy><node text="我的" /></hierarchy>'


class ScreenInfoStub(AdbClient):
    def __init__(self) -> None:
        super().__init__(Path("C:/adb.exe"))

    def shell(self, *arguments: str, check: bool = True) -> str:
        if arguments == ("wm", "size"):
            return "Physical size: 1080x1920"
        if arguments == ("wm", "density"):
            return "Physical density: 480"
        raise AssertionError(arguments)


class LaunchCommandStub(AdbClient):
    def __init__(self) -> None:
        super().__init__(Path("C:/adb.exe"), serial="emulator-5556")
        self.calls: list[tuple[str, ...]] = []

    def shell(self, *arguments: str, check: bool = True) -> str:
        self.calls.append(arguments)
        return ""


class ReconnectAdb(AdbClient):
    def __init__(self, state: str = "device") -> None:
        super().__init__(Path("C:/adb.exe"), serial="127.0.0.1:16416")
        self.state = state
        self.connect_calls: list[str] = []

    def connect(self, address: str) -> str:
        self.connect_calls.append(address)
        return "connected"

    def list_devices(self) -> list[Device]:
        return [Device(self.serial, self.state)]


class AdbTests(unittest.TestCase):
    def test_run_builds_device_command_and_returns_trimmed_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "adb.exe"
            executable.write_bytes(b"")
            completed = subprocess.CompletedProcess(
                [str(executable)],
                0,
                stdout="  ok\n",
                stderr="",
            )
            with patch("free_app.adb.subprocess.run", return_value=completed) as run:
                adb = AdbClient(executable, serial="127.0.0.1:16416", command_timeout=7)
                self.assertEqual(adb.shell("echo", "ok"), "ok")

        self.assertEqual(
            run.call_args.args[0],
            [str(executable), "-s", "127.0.0.1:16416", "shell", "echo", "ok"],
        )
        self.assertEqual(run.call_args.kwargs["timeout"], 7)
        self.assertFalse(run.call_args.kwargs["check"])

    def test_run_surfaces_nonzero_exit_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "adb.exe"
            executable.write_bytes(b"")
            adb = AdbClient(executable)
            failed = subprocess.CompletedProcess(
                [str(executable)],
                1,
                stdout="",
                stderr="permission denied",
            )
            with patch("free_app.adb.subprocess.run", return_value=failed):
                with self.assertRaisesRegex(AdbError, "permission denied"):
                    adb._run(["devices"])

            with (
                patch(
                    "free_app.adb.subprocess.run",
                    side_effect=subprocess.TimeoutExpired([str(executable)], 1),
                ),
                self.assertRaisesRegex(AdbError, "ADB"),
            ):
                adb._run(["devices"])

    def test_screenshot_builds_exec_out_command_and_returns_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "adb.exe"
            executable.write_bytes(b"")
            completed = subprocess.CompletedProcess(
                [str(executable)],
                0,
                stdout=b"png-bytes",
                stderr=b"",
            )
            with patch("free_app.adb.subprocess.run", return_value=completed) as run:
                adb = AdbClient(executable, serial="127.0.0.1:16416")
                self.assertEqual(adb.screenshot(), b"png-bytes")

        self.assertEqual(
            run.call_args.args[0],
            [
                str(executable),
                "-s",
                "127.0.0.1:16416",
                "exec-out",
                "screencap",
                "-p",
            ],
        )

    def test_screen_info_rejects_unparseable_output(self) -> None:
        adb = ScreenInfoStub()

        with patch.object(adb, "shell", side_effect=["unexpected", "unexpected"]):
            with self.assertRaisesRegex(AdbError, "size"):
                adb.screen_info()

    def test_dump_ui_runs_single_shell_script_and_reads_xml(self) -> None:
        adb = DumpStub()
        xml = adb.dump_ui()
        self.assertIn("<hierarchy>", xml)
        self.assertEqual(len(adb.calls), 1)
        arguments, check = adb.calls[0]
        self.assertEqual(arguments[:2], ("sh", "-c"))
        script = arguments[2]
        self.assertIn("rm -f", script)
        self.assertIn("uiautomator dump", script)
        self.assertIn("cat ", script)
        self.assertFalse(check)

    def test_dump_ui_rejects_missing_hierarchy(self) -> None:
        class EmptyDumpStub(AdbClient):
            def __init__(self) -> None:
                super().__init__(Path("C:/adb.exe"))

            def shell(self, *arguments: str, check: bool = True) -> str:
                return "not xml"

        with self.assertRaisesRegex(AdbError, "hierarchy"):
            EmptyDumpStub().dump_ui()

    def test_device_list_parses_ready_and_offline_states(self) -> None:
        adb = StubAdb(
            "List of devices attached\n"
            "emulator-5556 offline transport_id:1\n"
            "emulator-5558 device product:test model:test device:test transport_id:2\n"
        )
        devices = adb.list_devices()
        self.assertEqual(
            [(device.serial, device.state) for device in devices],
            [
                ("emulator-5556", "offline"),
                ("emulator-5558", "device"),
            ],
        )

    def test_select_device_requires_an_explicit_target(self) -> None:
        adb = StubAdb(
            "List of devices attached\n"
            "emulator-5558 device product:test model:test device:test transport_id:2\n"
        )
        with self.assertRaisesRegex(AdbError, "目标 ADB serial"):
            adb.select_device()

    def test_missing_preferred_device_fails_even_when_other_devices_are_ready(
        self,
    ) -> None:
        adb = StubAdb(
            "List of devices attached\n"
            "emulator-5558 device product:test model:test device:test transport_id:2\n"
        )
        with self.assertRaisesRegex(AdbError, "找不到目标 ADB 设备 emulator-5556"):
            adb.select_device("emulator-5556")

    def test_missing_preferred_device_fails_without_any_devices(self) -> None:
        adb = StubAdb("List of devices attached\n")
        with self.assertRaisesRegex(AdbError, "找不到目标 ADB 设备 emulator-5556"):
            adb.select_device("emulator-5556")

    def test_screenshot_requires_selected_device(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "adb.exe"
            executable.write_bytes(b"")
            with self.assertRaisesRegex(AdbError, "尚未选择"):
                AdbClient(executable).screenshot()

    def test_preferred_offline_device_is_not_silently_replaced(self) -> None:
        adb = StubAdb(
            "List of devices attached\n"
            "emulator-5556 offline transport_id:1\n"
            "emulator-5558 device product:test model:test device:test transport_id:2\n"
        )
        with self.assertRaisesRegex(AdbError, "offline"):
            adb.select_device("emulator-5556")

    def test_screen_info_parses_fixed_mumu_size_and_density(self) -> None:
        info = ScreenInfoStub().screen_info()
        self.assertEqual((info.width, info.height, info.density), (1080, 1920, 480))

    def test_current_package_reads_mumu_top_resumed_activity(self) -> None:
        adb = StubAdb(
            "topResumedActivity=ActivityRecord{123 u0 tv.danmaku.bili/.MainActivityV2 t33}\n"
            "ResumedActivity: ActivityRecord{123 u0 tv.danmaku.bili/.MainActivityV2 t33}"
        )
        self.assertEqual(adb.current_package(), "tv.danmaku.bili")

    def test_launch_targets_the_launcher_activity_explicitly(self) -> None:
        adb = LaunchCommandStub()

        adb.launch("tv.danmaku.bili")

        self.assertEqual(
            adb.calls[0],
            (
                "monkey",
                "-p",
                "tv.danmaku.bili",
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ),
        )

    def test_preview_truncates_and_compacts_whitespace(self) -> None:
        self.assertEqual(AdbClient._preview("a   b"), "a b")
        preview = AdbClient._preview("x" * 300)
        self.assertEqual(len(preview), 241)
        self.assertTrue(preview.endswith("…"))

    def test_trace_never_raises_when_log_callback_fails(self) -> None:
        def broken_callback(_message: str) -> None:
            raise RuntimeError("log broken")

        adb = AdbClient(Path("C:/adb.exe"), log_callback=broken_callback)
        adb._trace("diagnostic")

    def test_run_rejects_missing_executable(self) -> None:
        adb = AdbClient(Path("C:/missing/adb.exe"))

        with self.assertRaisesRegex(AdbError, "找不到 ADB"):
            adb._run(["devices"])

    def test_list_devices_ignores_malformed_lines(self) -> None:
        adb = StubAdb("List of devices attached\nonly-one-field\nsomething unknown\n")

        self.assertEqual(adb.list_devices(), [])

    def test_select_device_returns_ready_device_and_sets_serial(self) -> None:
        adb = StubAdb(
            "List of devices attached\n"
            "emulator-5556 device product:test transport_id:2\n"
        )

        selected = adb.select_device("emulator-5556")

        self.assertEqual(selected.serial, "emulator-5556")
        self.assertEqual(adb.serial, "emulator-5556")

    def test_simple_adb_commands_forward_arguments(self) -> None:
        adb = LaunchCommandStub()

        adb.force_stop("demo.package")
        adb.press_back()
        adb.tap(10, 20)
        adb.swipe(0, 0, 100, 200, 500)

        self.assertEqual(
            adb.calls,
            [
                ("am", "force-stop", "demo.package"),
                ("input", "keyevent", "KEYCODE_BACK"),
                ("input", "tap", "10", "20"),
                ("input", "swipe", "0", "0", "100", "200", "500"),
            ],
        )

    def test_connect_and_exec_out_use_run(self) -> None:
        adb = StubAdb("ok")

        self.assertEqual(adb.connect("127.0.0.1:16416"), "ok")
        self.assertEqual(adb.exec_out("cat", "/tmp/x"), "ok")

    def test_reconnect_returns_true_for_ready_network_device(self) -> None:
        adb = ReconnectAdb()

        self.assertTrue(adb.reconnect())
        self.assertEqual(adb.connect_calls, ["127.0.0.1:16416"])

    def test_reconnect_skips_local_serial_without_connect(self) -> None:
        adb = AdbClient(Path("C:/adb.exe"), serial="emulator-5556")

        with patch.object(adb, "connect") as connect:
            self.assertFalse(adb.reconnect())

        connect.assert_not_called()

    def test_reconnect_returns_false_when_device_stays_offline(self) -> None:
        adb = ReconnectAdb(state="offline")

        self.assertFalse(adb.reconnect())
        self.assertEqual(adb.connect_calls, ["127.0.0.1:16416"])

    def test_reconnect_returns_false_when_connect_fails(self) -> None:
        adb = ReconnectAdb()

        def fail_connect(_address: str) -> str:
            raise AdbError("cannot connect")

        adb.connect = fail_connect

        self.assertFalse(adb.reconnect())

    def test_screenshot_reports_timeout_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "adb.exe"
            executable.write_bytes(b"")
            adb = AdbClient(executable, serial="emulator-5556")

            with (
                patch(
                    "free_app.adb.subprocess.run",
                    side_effect=subprocess.TimeoutExpired([str(executable)], 1),
                ),
                self.assertRaisesRegex(AdbError, "截图命令超时"),
            ):
                adb.screenshot()

            failed = subprocess.CompletedProcess(
                [str(executable)],
                1,
                stdout=b"",
                stderr=b"device offline",
            )
            with patch("free_app.adb.subprocess.run", return_value=failed):
                with self.assertRaisesRegex(AdbError, "device offline"):
                    adb.screenshot()

    def test_current_package_returns_none_when_no_marker_matches(self) -> None:
        adb = StubAdb("no useful line\n")

        self.assertIsNone(adb.current_package())
