from __future__ import annotations

import json
import subprocess
import tempfile
from threading import Event
import unittest
from pathlib import Path
from unittest.mock import patch

from free_app.adb import AdbError, Device
from free_app.mumu import (
    DEFAULT_MUMU_CLI,
    MuMuController,
    MuMuError,
    MuMuStopRequested,
    connect_to_running_mumu,
    mumu_adb_address,
    mumu_cli_path,
    prepare_device,
    shutdown_mumu,
    shutdown_mumu_app,
)


class FakeAdb:
    serial: str | None = None

    def __init__(self) -> None:
        self.connected: list[str] = []

    def connect(self, address: str) -> str:
        self.connected.append(address)
        return "connected"

    def list_devices(self) -> list[Device]:
        return [Device("127.0.0.1:16416", "device")]


class MumuTests(unittest.TestCase):
    def test_controller_surfaces_timeout_and_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "mumu-cli.exe"
            executable.write_bytes(b"")
            controller = MuMuController(executable, command_timeout=3)
            failed = subprocess.CompletedProcess(
                [str(executable)],
                1,
                stdout="",
                stderr="cli failed",
            )
            with patch("free_app.mumu.subprocess.run", return_value=failed):
                with self.assertRaisesRegex(MuMuError, "cli failed"):
                    controller.launch("0")

            with patch(
                "free_app.mumu.subprocess.run",
                side_effect=subprocess.TimeoutExpired([str(executable)], 3),
            ):
                with self.assertRaisesRegex(MuMuError, "MuMu CLI"):
                    controller.shutdown("0")

    def test_controller_run_rejects_missing_executable(self) -> None:
        controller = MuMuController(Path("C:/missing/mumu-cli.exe"))

        with self.assertRaisesRegex(MuMuError, "找不到 MuMu CLI"):
            controller.launch("0")

    def test_controller_list_instances_rejects_invalid_json(self) -> None:
        controller = MuMuController(Path("C:/mumu-cli.exe"))
        with patch.object(controller, "_run", return_value="not-json"):
            with self.assertRaises(MuMuError):
                controller.list_instances()

    def test_controller_rejects_invalid_instance_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "mumu-cli.exe"
            executable.write_bytes(b"")
            completed = subprocess.CompletedProcess(
                [str(executable)],
                0,
                stdout="not-json",
                stderr="",
            )
            with patch("free_app.mumu.subprocess.run", return_value=completed):
                with self.assertRaises(MuMuError):
                    MuMuController(executable).instance_info("0")

    def test_controller_rejects_non_object_instance_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "mumu-cli.exe"
            executable.write_bytes(b"")
            completed = subprocess.CompletedProcess(
                [str(executable)],
                0,
                stdout="[]",
                stderr="",
            )
            with patch("free_app.mumu.subprocess.run", return_value=completed):
                with self.assertRaises(MuMuError):
                    MuMuController(executable).instance_info("0")

    def test_mumu_adb_address_uses_reported_host_and_port(self) -> None:
        self.assertEqual(
            mumu_adb_address({"adb_host_ip": "127.0.0.1", "adb_port": 16416}),
            "127.0.0.1:16416",
        )
        self.assertIsNone(mumu_adb_address({"adb_port": 0}))

    def test_connect_to_running_mumu_connects_without_starting(self) -> None:
        adb = FakeAdb()
        with patch(
            "free_app.mumu.MuMuController.instance_info",
            return_value={"adb_host_ip": "127.0.0.1", "adb_port": 16416},
        ):
            device = connect_to_running_mumu(
                adb,
                {"mumu_vm_index": "1", "auto_start_mumu": True},
            )

        self.assertEqual(adb.serial, "127.0.0.1:16416")
        self.assertEqual(adb.connected, ["127.0.0.1:16416"])
        self.assertEqual(device.serial, "127.0.0.1:16416")

    def test_connect_to_running_mumu_requires_adb_address(self) -> None:
        adb = FakeAdb()
        with patch(
            "free_app.mumu.MuMuController.instance_info",
            return_value={"adb_host_ip": "127.0.0.1", "adb_port": 0},
        ):
            with self.assertRaisesRegex(AdbError, "未返回动态 ADB 地址"):
                connect_to_running_mumu(adb, {"mumu_vm_index": "1"})

    def test_connect_to_running_mumu_uses_default_timeout_on_bad_setting(self) -> None:
        adb = FakeAdb()
        with patch(
            "free_app.mumu.MuMuController.instance_info",
            return_value={"adb_host_ip": "127.0.0.1", "adb_port": 0},
        ):
            with self.assertRaises(AdbError):
                connect_to_running_mumu(
                    adb,
                    {
                        "mumu_vm_index": "1",
                        "mumu_command_timeout_seconds": "bad",
                    },
                )

    def test_connect_to_running_mumu_rejects_device_not_online(self) -> None:
        class NoDeviceAdb:
            serial: str | None = None

            def connect(self, address: str) -> str:
                return "connected"

            def list_devices(self) -> list[Device]:
                return []

        adb = NoDeviceAdb()
        with patch(
            "free_app.mumu.MuMuController.instance_info",
            return_value={"adb_host_ip": "127.0.0.1", "adb_port": 16416},
        ):
            with self.assertRaisesRegex(AdbError, "ADB 设备未上线"):
                connect_to_running_mumu(adb, {"mumu_vm_index": "1"})

    def test_controller_builds_instance_launch_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "mumu-cli.exe"
            executable.write_bytes(b"")
            completed = subprocess.CompletedProcess(
                [str(executable)],
                0,
                stdout="",
                stderr="",
            )
            with patch("free_app.mumu.subprocess.run", return_value=completed) as run:
                MuMuController(executable).launch("1")

        command = run.call_args.args[0]
        self.assertEqual(command, [str(executable), "control", "--vmindex", "1", "launch"])

    def test_controller_builds_instance_shutdown_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "mumu-cli.exe"
            executable.write_bytes(b"")
            completed = subprocess.CompletedProcess(
                [str(executable)],
                0,
                stdout="",
                stderr="",
            )
            with patch("free_app.mumu.subprocess.run", return_value=completed) as run:
                MuMuController(executable).shutdown("1")

        command = run.call_args.args[0]
        self.assertEqual(command, [str(executable), "control", "--vmindex", "1", "shutdown"])

    def test_controller_builds_main_close_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "mumu-cli.exe"
            executable.write_bytes(b"")
            completed = subprocess.CompletedProcess(
                [str(executable)],
                0,
                stdout="",
                stderr="",
            )
            with patch("free_app.mumu.subprocess.run", return_value=completed) as run:
                MuMuController(executable).close_main()

        command = run.call_args.args[0]
        self.assertEqual(command, [str(executable), "main", "close"])

    def test_controller_lists_instances_from_all_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "mumu-cli.exe"
            executable.write_bytes(b"")
            completed = subprocess.CompletedProcess(
                [str(executable)],
                0,
                stdout=json.dumps(
                    {
                        "0": {"name": "BA"},
                        "1": {"name": "iPhone100"},
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )
            with patch("free_app.mumu.subprocess.run", return_value=completed) as run:
                instances = MuMuController(executable).list_instances()

        command = run.call_args.args[0]
        self.assertEqual(command, [str(executable), "info", "--vmindex", "all"])
        self.assertEqual(instances, {0: "BA", 1: "iPhone100"})

    def test_mumu_cli_path_prefers_folder_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "MuMu"
            nx_main = root / "nx_main"
            nx_main.mkdir(parents=True)
            executable = nx_main / "mumu-cli.exe"
            executable.write_bytes(b"")

            resolved = mumu_cli_path({"mumu_directory": str(root)})

        self.assertEqual(resolved, executable)

    def test_mumu_cli_path_falls_back_to_root_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "MuMu"
            root.mkdir(parents=True)
            executable = root / "mumu-cli.exe"
            executable.write_bytes(b"")

            resolved = mumu_cli_path({"mumu_directory": str(root)})

        self.assertEqual(resolved, executable)

    def test_mumu_cli_path_falls_back_to_configured_value(self) -> None:
        resolved = mumu_cli_path({"mumu_cli_path": "C:/mumu-cli.exe"})
        self.assertEqual(resolved, Path("C:/mumu-cli.exe"))

    def test_mumu_cli_path_uses_default_when_nothing_is_configured(self) -> None:
        self.assertEqual(mumu_cli_path({}), DEFAULT_MUMU_CLI)

    def test_controller_lists_instances_with_fallback_names_and_skips_bad_keys(self) -> None:
        controller = MuMuController(Path("C:/mumu-cli.exe"))
        with patch.object(
            controller,
            "_run",
            return_value=json.dumps(
                {
                    "2": {},
                    "3": {"name": ""},
                    "4": "not-an-object",
                    "bad": {"name": "ignored"},
                }
            ),
        ):
            self.assertEqual(
                controller.list_instances(),
                {2: "2", 3: "3", 4: "4"},
            )

    def test_shutdown_mumu_logs_failure_and_returns_false(self) -> None:
        controller = type(
            "ControllerStub",
            (),
            {"shutdown": lambda self, _vmindex: (_ for _ in ()).throw(MuMuError("shutdown failed"))},
        )()
        logs: list[str] = []
        with patch("free_app.mumu.MuMuController", return_value=controller):
            self.assertFalse(
                shutdown_mumu(
                    {"close_mumu_after_run": True, "mumu_cli_path": "C:/mumu-cli.exe"},
                    logs.append,
                )
            )

        self.assertTrue(any("shutdown failed" in message for message in logs))

    def test_shutdown_mumu_uses_defaults_for_invalid_timeouts(self) -> None:
        controller = type(
            "ControllerStub",
            (),
            {
                "shutdown": lambda self, _vmindex: "accepted",
                "instance_info": lambda self, _vmindex: {
                    "is_process_started": False,
                    "is_android_started": False,
                },
            },
        )()
        logs: list[str] = []

        with patch("free_app.mumu.MuMuController", return_value=controller):
            self.assertTrue(
                shutdown_mumu(
                    {
                        "close_mumu_after_run": True,
                        "mumu_cli_path": "C:/mumu-cli.exe",
                        "mumu_command_timeout_seconds": "bad",
                        "mumu_poll_interval_seconds": "bad",
                    },
                    logs.append,
                )
            )

    def test_shutdown_mumu_retries_after_invalid_state(self) -> None:
        class FlakyController:
            def __init__(self) -> None:
                self.info_calls = 0

            def shutdown(self, _vmindex: str) -> str:
                return "accepted"

            def instance_info(self, _vmindex: str) -> dict[str, bool] | list[object]:
                self.info_calls += 1
                if self.info_calls == 1:
                    return []
                return {
                    "is_process_started": False,
                    "is_android_started": False,
                }

        controller = FlakyController()
        logs: list[str] = []
        with patch("free_app.mumu.MuMuController", return_value=controller):
            self.assertTrue(
                shutdown_mumu(
                    {
                        "close_mumu_after_run": True,
                        "mumu_cli_path": "C:/mumu-cli.exe",
                        "mumu_command_timeout_seconds": 1,
                        "mumu_poll_interval_seconds": 0,
                    },
                    logs.append,
                    sleep_function=lambda _seconds: None,
                )
            )

        self.assertTrue(any("格式错误" in message for message in logs))

    def test_shutdown_mumu_uses_configured_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "mumu-cli.exe"
            executable.write_bytes(b"")
            controller = type(
                "ControllerStub",
                (),
                {
                    "shutdown": lambda self, vmindex: vmindex,
                    "instance_info": lambda self, _vmindex: {
                        "is_process_started": False,
                        "is_android_started": False,
                    },
                },
            )()
            logs: list[str] = []
            with patch("free_app.mumu.MuMuController", return_value=controller) as controller_type:
                self.assertTrue(
                    shutdown_mumu(
                        {
                            "close_mumu_after_run": True,
                            "mumu_vm_index": 1,
                            "mumu_cli_path": str(executable),
                        },
                        logs.append,
                    )
                )

        controller_type.assert_called_once()
        self.assertTrue(any("关闭 MuMu 实例 1" in message for message in logs))

    def test_shutdown_mumu_waits_for_instance_to_stop(self) -> None:
        class DelayedController:
            def __init__(self) -> None:
                self.info_calls = 0
                self.shutdown_calls = 0

            def shutdown(self, _vmindex: str) -> str:
                self.shutdown_calls += 1
                return "accepted"

            def instance_info(self, _vmindex: str) -> dict[str, bool]:
                self.info_calls += 1
                return {
                    "is_process_started": self.info_calls < 2,
                    "is_android_started": self.info_calls < 2,
                }

        controller = DelayedController()
        logs: list[str] = []
        with patch("free_app.mumu.MuMuController", return_value=controller):
            self.assertTrue(
                shutdown_mumu(
                    {
                        "close_mumu_after_run": True,
                        "mumu_vm_index": 1,
                        "mumu_cli_path": "C:/mumu-cli.exe",
                        "mumu_command_timeout_seconds": 1,
                        "mumu_poll_interval_seconds": 0,
                    },
                    logs.append,
                    sleep_function=lambda _seconds: None,
                )
            )

        self.assertEqual(controller.info_calls, 2)
        self.assertEqual(controller.shutdown_calls, 2)
        self.assertTrue(any("关闭后 MuMu 状态: process=True" in message for message in logs))
        self.assertTrue(any("已关闭 MuMu 实例 1" in message for message in logs))

    def test_shutdown_mumu_reports_timeout_when_process_stays_running(self) -> None:
        controller = type(
            "ControllerStub",
            (),
            {
                "shutdown": lambda self, _vmindex: "accepted",
                "instance_info": lambda self, _vmindex: {
                    "is_process_started": True,
                    "is_android_started": True,
                },
            },
        )()
        logs: list[str] = []
        with patch("free_app.mumu.MuMuController", return_value=controller):
            self.assertFalse(
                shutdown_mumu(
                    {
                        "close_mumu_after_run": True,
                        "mumu_cli_path": "C:/mumu-cli.exe",
                        "mumu_command_timeout_seconds": 0,
                    },
                    logs.append,
                )
            )

        self.assertTrue(any("超时" in message and "仍在运行" in message for message in logs))

    def test_shutdown_mumu_is_disabled_by_default(self) -> None:
        with patch("free_app.mumu.MuMuController") as controller_type:
            self.assertFalse(shutdown_mumu({}, lambda _message: None))

        controller_type.assert_not_called()

    def test_shutdown_mumu_app_is_disabled_by_default(self) -> None:
        with patch("free_app.mumu.MuMuController") as controller_type:
            self.assertFalse(shutdown_mumu_app({}, lambda _message: None))

        controller_type.assert_not_called()

    def test_shutdown_mumu_app_calls_main_close(self) -> None:
        controller = type(
            "ControllerStub",
            (),
            {"close_main": lambda self: "closed"},
        )()
        logs: list[str] = []
        with patch("free_app.mumu.MuMuController", return_value=controller):
            self.assertTrue(
                shutdown_mumu_app(
                    {
                        "close_mumu_app_after_run": True,
                        "mumu_cli_path": "C:/mumu-cli.exe",
                    },
                    logs.append,
                )
            )

        self.assertTrue(any("退出 MuMu 软件程序" in message for message in logs))
        self.assertTrue(any("已请求退出" in message for message in logs))

    def test_shutdown_mumu_app_logs_close_failure(self) -> None:
        controller = type(
            "ControllerStub",
            (),
            {
                "close_main": lambda self: (_ for _ in ()).throw(
                    MuMuError("close failed")
                )
            },
        )()
        logs: list[str] = []
        with patch("free_app.mumu.MuMuController", return_value=controller):
            self.assertFalse(
                shutdown_mumu_app(
                    {
                        "close_mumu_app_after_run": True,
                        "mumu_cli_path": "C:/mumu-cli.exe",
                    },
                    logs.append,
                )
            )

        self.assertTrue(any("close failed" in message for message in logs))

    def test_shutdown_mumu_app_uses_default_timeout_on_bad_setting(self) -> None:
        controller = type(
            "ControllerStub",
            (),
            {"close_main": lambda self: "closed"},
        )()
        logs: list[str] = []
        with patch("free_app.mumu.MuMuController", return_value=controller):
            self.assertTrue(
                shutdown_mumu_app(
                    {
                        "close_mumu_app_after_run": True,
                        "mumu_cli_path": "C:/mumu-cli.exe",
                        "mumu_command_timeout_seconds": "bad",
                    },
                    logs.append,
                )
            )

    def test_prepare_device_starts_instance_and_discovers_dynamic_address(self) -> None:
        adb = FakeAdb()
        controller = type(
            "ControllerStub",
            (),
            {
                "instance_info": lambda self, _vmindex: {
                    "is_process_started": False,
                    "is_android_started": False,
                    "adb_host_ip": "127.0.0.1",
                    "adb_port": 16416,
                },
                "launch": lambda self, _vmindex: "",
            },
        )()
        settings = {
            "auto_start_mumu": True,
            "mumu_vm_index": 1,
            "mumu_cli_path": "C:/mumu-cli.exe",
            "mumu_start_timeout_seconds": 42,
            "mumu_poll_interval_seconds": 2,
        }
        logs: list[str] = []
        with patch("free_app.mumu.MuMuController", return_value=controller):
            device = prepare_device(adb, settings, logs.append)

        self.assertEqual(device.serial, "127.0.0.1:16416")
        self.assertTrue(any("启动 MuMu 实例 1" in message for message in logs))

    def test_prepare_device_without_auto_start_also_discovers_dynamic_address(self) -> None:
        controller = type(
            "ControllerStub",
            (),
            {
                "instance_info": lambda self, _vmindex: {
                    "is_process_started": True,
                    "is_android_started": True,
                    "adb_host_ip": "127.0.0.1",
                    "adb_port": 16416,
                },
            },
        )()
        adb = FakeAdb()
        with patch("free_app.mumu.MuMuController", return_value=controller):
            device = prepare_device(
                adb,
                {
                    "auto_start_mumu": False,
                    "mumu_vm_index": 1,
                    "mumu_cli_path": "C:/mumu-cli.exe",
                },
            )
        self.assertEqual(device.serial, "127.0.0.1:16416")
        self.assertEqual(adb.connected, ["127.0.0.1:16416"])

    def test_prepare_device_without_auto_start_reports_initial_state_failure(self) -> None:
        class NoDevicesAdb:
            serial: str | None = None

            def list_devices(self) -> list[Device]:
                return []

        controller = type(
            "ControllerStub",
            (),
            {
                "instance_info": lambda self, _vmindex: (_ for _ in ()).throw(
                    MuMuError("info failed")
                )
            },
        )()
        logs: list[str] = []
        with patch("free_app.mumu.MuMuController", return_value=controller):
            with self.assertRaisesRegex(AdbError, "动态 ADB 地址"):
                prepare_device(
                    NoDevicesAdb(),
                    {
                        "auto_start_mumu": False,
                        "mumu_vm_index": 1,
                        "mumu_cli_path": "C:/mumu-cli.exe",
                        "mumu_start_timeout_seconds": 0,
                        "mumu_poll_interval_seconds": 0,
                    },
                    logs.append,
                )

        self.assertTrue(any("读取 MuMu 实例状态失败" in message for message in logs))
        self.assertTrue(any("自动启动已关闭" in message for message in logs))

    def test_prepare_device_tolerates_instance_info_and_device_list_failures(self) -> None:
        class FlakyAdb:
            serial: str | None = None

            def list_devices(self) -> list[Device]:
                raise AdbError("device list failed")

        class FlakyController:
            def __init__(self) -> None:
                self.info_calls = 0

            def instance_info(self, _vmindex: str) -> dict[str, object]:
                self.info_calls += 1
                if self.info_calls <= 2:
                    return {
                        "is_process_started": True,
                        "is_android_started": True,
                        "adb_host_ip": "127.0.0.1",
                        "adb_port": 16416,
                    }
                raise MuMuError("info failed")

        adb = FlakyAdb()
        controller = FlakyController()
        logs: list[str] = []
        with patch("free_app.mumu.MuMuController", return_value=controller):
            with self.assertRaisesRegex(AdbError, "等待 MuMu ADB 设备超时"):
                prepare_device(
                    adb,
                    {
                        "auto_start_mumu": False,
                        "mumu_vm_index": 1,
                        "mumu_cli_path": "C:/mumu-cli.exe",
                        "mumu_start_timeout_seconds": 1,
                        "mumu_poll_interval_seconds": 0,
                    },
                    logs.append,
                )

        self.assertTrue(any("实例信息读取失败" in message for message in logs))
        self.assertTrue(any("ADB 设备列表读取失败" in message for message in logs))

    def test_prepare_device_honors_stop_before_selecting_device(self) -> None:
        class SelectAdb:
            def select_device(self, _serial: str | None) -> Device:
                raise AssertionError("ADB 不应在停止后继续选择设备")

        stop_event = Event()
        stop_event.set()
        with self.assertRaises(MuMuStopRequested):
            prepare_device(
                SelectAdb(),
                {"auto_start_mumu": False},
                stop_event=stop_event,
            )

    def test_prepare_device_honors_stop_during_poll_wait(self) -> None:
        stop_event = Event()

        class PollAdb:
            serial = "emulator-5556"

            def list_devices(self) -> list[Device]:
                stop_event.set()
                return []

        controller = type(
            "ControllerStub",
            (),
            {
                "instance_info": lambda self, _vmindex: {
                    "is_process_started": True,
                    "is_android_started": False,
                },
            },
        )()
        settings = {
            "auto_start_mumu": True,
            "mumu_vm_index": 1,
            "mumu_cli_path": "C:/mumu-cli.exe",
            "mumu_start_timeout_seconds": 30,
            "mumu_poll_interval_seconds": 10,
        }

        with patch("free_app.mumu.MuMuController", return_value=controller):
            with self.assertRaises(MuMuStopRequested):
                prepare_device(PollAdb(), settings, stop_event=stop_event)

    def test_prepare_device_discovers_mumu_forwarded_adb_port(self) -> None:
        class PortAdb:
            serial = "emulator-5556"

            def __init__(self) -> None:
                self.connected: list[str] = []

            def connect(self, address: str) -> str:
                self.connected.append(address)
                return "connected"

            def list_devices(self) -> list[Device]:
                return [Device("127.0.0.1:16416", "device")]

        adb = PortAdb()
        controller = type(
            "ControllerStub",
            (),
            {
                "instance_info": lambda self, _vmindex: {
                    "is_process_started": True,
                    "is_android_started": True,
                    "adb_host_ip": "127.0.0.1",
                    "adb_port": 16416,
                },
            },
        )()
        settings = {
            "auto_start_mumu": True,
            "mumu_vm_index": 1,
            "mumu_cli_path": "C:/mumu-cli.exe",
            "mumu_start_timeout_seconds": 42,
            "mumu_poll_interval_seconds": 2,
        }
        with patch("free_app.mumu.MuMuController", return_value=controller):
            device = prepare_device(adb, settings)

        self.assertEqual(device.serial, "127.0.0.1:16416")
        self.assertEqual(adb.connected, ["127.0.0.1:16416"])

    def test_prepare_device_retries_a_transient_adb_connect_failure(self) -> None:
        class FlakyAdb:
            serial: str | None = None

            def __init__(self) -> None:
                self.connect_calls = 0

            def connect(self, _address: str) -> str:
                self.connect_calls += 1
                if self.connect_calls == 1:
                    raise AdbError("port not ready")
                return "connected"

            def list_devices(self) -> list[Device]:
                if self.connect_calls < 2:
                    return []
                return [Device("127.0.0.1:16416", "device")]

        controller = type(
            "ControllerStub",
            (),
            {
                "instance_info": lambda self, _vmindex: {
                    "is_process_started": True,
                    "is_android_started": True,
                    "adb_host_ip": "127.0.0.1",
                    "adb_port": 16416,
                },
            },
        )()
        adb = FlakyAdb()
        logs: list[str] = []
        with patch("free_app.mumu.MuMuController", return_value=controller):
            device = prepare_device(
                adb,
                {
                    "auto_start_mumu": False,
                    "mumu_vm_index": 0,
                    "mumu_cli_path": "C:/mumu-cli.exe",
                    "mumu_start_timeout_seconds": 1,
                    "mumu_poll_interval_seconds": 0,
                },
                logs.append,
            )

        self.assertEqual(device.serial, "127.0.0.1:16416")
        self.assertEqual(adb.connect_calls, 2)
        self.assertTrue(any("port not ready" in message for message in logs))

    def test_prepare_device_waits_for_dynamic_port_after_launch(self) -> None:
        class DynamicAdb:
            serial = "emulator-5556"

            def __init__(self) -> None:
                self.connected: list[str] = []

            def connect(self, address: str) -> str:
                self.connected.append(address)
                return "connected"

            def list_devices(self) -> list[Device]:
                if self.connected:
                    return [Device(self.connected[-1], "device")]
                return []

        class DynamicController:
            def __init__(self) -> None:
                self.info_calls = 0
                self.launched = False

            def instance_info(self, _vmindex: str) -> dict[str, object]:
                self.info_calls += 1
                if self.info_calls == 1:
                    return {"is_process_started": False, "is_android_started": False}
                if self.info_calls == 2:
                    return {"is_process_started": True, "is_android_started": False}
                return {
                    "is_process_started": True,
                    "is_android_started": True,
                    "adb_host_ip": "127.0.0.1",
                    "adb_port": 16416,
                }

            def launch(self, _vmindex: str) -> str:
                self.launched = True
                return ""

        adb = DynamicAdb()
        controller = DynamicController()
        settings = {
            "auto_start_mumu": True,
            "mumu_vm_index": 1,
            "mumu_cli_path": "C:/mumu-cli.exe",
            "mumu_start_timeout_seconds": 42,
            "mumu_poll_interval_seconds": 0,
        }
        with patch("free_app.mumu.MuMuController", return_value=controller):
            device = prepare_device(adb, settings)

        self.assertTrue(controller.launched)
        self.assertEqual(device.serial, "127.0.0.1:16416")
        self.assertEqual(adb.connected, ["127.0.0.1:16416"])

    def test_prepare_device_fails_when_dynamic_target_never_comes_online(self) -> None:
        class OtherReadyAdb:
            serial = "emulator-5556"

            def __init__(self) -> None:
                self.connected: list[str] = []

            def connect(self, address: str) -> str:
                self.connected.append(address)
                return "connected"

            def list_devices(self) -> list[Device]:
                # Another ready device exists, but it must never be selected.
                return [Device("emulator-5556", "device")]

        adb = OtherReadyAdb()
        controller = type(
            "ControllerStub",
            (),
            {
                "instance_info": lambda self, _vmindex: {
                    "is_process_started": True,
                    "is_android_started": True,
                    "adb_host_ip": "127.0.0.1",
                    "adb_port": 16416,
                },
            },
        )()
        settings = {
            "auto_start_mumu": True,
            "mumu_vm_index": 1,
            "mumu_cli_path": "C:/mumu-cli.exe",
            "mumu_start_timeout_seconds": 0.001,
            "mumu_poll_interval_seconds": 0,
        }

        with patch("free_app.mumu.MuMuController", return_value=controller):
            with self.assertRaisesRegex(AdbError, "等待 MuMu ADB 设备超时: 127.0.0.1:16416"):
                prepare_device(adb, settings)

        self.assertEqual(adb.connected, ["127.0.0.1:16416"])

    def test_prepare_device_requires_dynamic_address(self) -> None:
        class ReadyAdb:
            def list_devices(self) -> list[Device]:
                return [Device("emulator-5556", "device")]

        controller = type(
            "ControllerStub",
            (),
            {
                "instance_info": lambda self, _vmindex: {
                    "is_process_started": True,
                    "is_android_started": True,
                },
            },
        )()
        settings = {
            "auto_start_mumu": True,
            "mumu_vm_index": 1,
            "mumu_cli_path": "C:/mumu-cli.exe",
            "mumu_start_timeout_seconds": 0,
            "mumu_poll_interval_seconds": 0,
        }

        with patch("free_app.mumu.MuMuController", return_value=controller):
            with self.assertRaisesRegex(AdbError, "动态 ADB 地址"):
                prepare_device(ReadyAdb(), settings)


if __name__ == "__main__":
    unittest.main()
