from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any

from .adb import AdbClient, AdbError, Device
from .constants import DEFAULT_MUMU_DIRECTORY
from .helpers import LogCallback, noop_log, number_setting

DEFAULT_MUMU_CLI = DEFAULT_MUMU_DIRECTORY / "nx_main" / "mumu-cli.exe"

# 安装目录内 adb.exe / mumu-cli.exe 的常见布局，按优先级排列（"" 表示根目录）。
_ADB_LAYOUT = ("nx_main", "shell", "")
_CLI_LAYOUT = ("nx_main", "")


class MuMuError(RuntimeError):
    pass


class MuMuStopRequested(RuntimeError):
    """MuMu 仍在准备时用户请求停止。"""


def _exe_candidates(folder: Path, name: str, subdirs: tuple[str, ...]) -> list[Path]:
    """安装目录下可执行文件的候选路径：各子目录布局加根目录。"""

    return [folder / subdir / name if subdir else folder / name for subdir in subdirs]


def adb_candidates(folder: Path) -> list[Path]:
    """adb.exe 在 MuMu 安装目录下的候选路径，按优先级排列。"""

    return _exe_candidates(folder, "adb.exe", _ADB_LAYOUT)


def cli_candidates(folder: Path) -> list[Path]:
    """mumu-cli.exe 在 MuMu 安装目录下的候选路径，按优先级排列。"""

    return _exe_candidates(folder, "mumu-cli.exe", _CLI_LAYOUT)


def resolve_adb_path(settings: dict[str, Any]) -> Path:
    """按设置推导 adb.exe：已配置路径 → 配置的安装目录布局 → 默认安装位置。

    候选都不存在时返回第一个候选，让后续 ADB 调用的报错暴露真实问题。
    """

    candidates: list[Path] = []
    configured = settings.get("adb_path")
    if isinstance(configured, str) and configured.strip():
        candidates.append(Path(configured.strip()))
    folder_value = settings.get("mumu_directory")
    if isinstance(folder_value, str) and folder_value.strip():
        candidates.extend(adb_candidates(Path(folder_value.strip())))
    candidates.extend(adb_candidates(DEFAULT_MUMU_DIRECTORY))
    return next((path for path in candidates if path.exists()), candidates[0])


def mumu_cli_path(settings: dict[str, Any]) -> Path:
    """按设置推导 mumu-cli.exe：已配置路径 → 配置的安装目录布局 → 默认安装位置。"""

    cli_value = settings.get("mumu_cli_path")
    if isinstance(cli_value, str) and cli_value.strip():
        return Path(cli_value.strip())
    folder_value = settings.get("mumu_directory")
    if isinstance(folder_value, str) and folder_value.strip():
        existing = next(
            (
                path
                for path in cli_candidates(Path(folder_value.strip()))
                if path.exists()
            ),
            None,
        )
        if existing:
            return existing
    return DEFAULT_MUMU_CLI


class MuMuController:
    def __init__(self, executable: Path, command_timeout: float = 30.0):
        self.executable = Path(executable)
        self.command_timeout = command_timeout

    def _run(self, arguments: list[str], timeout: float | None = None) -> str:
        if not self.executable.exists():
            raise MuMuError(f"找不到 MuMu CLI: {self.executable}")
        command = [str(self.executable), *arguments]
        child_environment = os.environ.copy()
        # 防止 PySide/离屏 Qt 变量改变 MuMu 命令行启动行为。
        for variable in ("QT_QPA_PLATFORM", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH"):
            child_environment.pop(variable, None)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.command_timeout if timeout is None else timeout,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
                env=child_environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise MuMuError(f"MuMu CLI 命令超时: {' '.join(command)}") from exc
        output = (completed.stdout or "").strip()
        error = (completed.stderr or "").strip()
        if completed.returncode != 0:
            raise MuMuError(error or output or f"exit code {completed.returncode}")
        return output

    def instance_info(self, vmindex: str) -> dict[str, Any]:
        output = self._run(["info", "--vmindex", str(vmindex)])
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise MuMuError(f"MuMu CLI 返回了无效实例信息: {output}") from exc
        if not isinstance(data, dict):
            raise MuMuError(f"MuMu CLI 实例信息格式错误: {output}")
        return data

    def list_instances(self) -> dict[int, str]:
        """返回所有已配置 MuMu 实例的 ``{vmindex: instance_name}`` 映射。"""

        # 列表查询用短超时（设置页每次打开都会调用），CLI 卡住时
        # 最多阻塞 3 秒而不是整段命令超时（10 秒）。
        output = self._run(
            ["info", "--vmindex", "all"], timeout=min(3.0, self.command_timeout)
        )
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise MuMuError(f"MuMu CLI 实例列表格式错误: {output}") from exc
        if not isinstance(data, dict):
            raise MuMuError(f"MuMu CLI 实例列表格式错误: {output}")
        instances: dict[int, str] = {}
        for index, details in data.items():
            name = details.get("name") if isinstance(details, dict) else None
            try:
                instances[int(index)] = str(name) if name else str(index)
            except (TypeError, ValueError):
                continue
        return instances

    def launch(self, vmindex: str) -> str:
        return self._run(["control", "--vmindex", str(vmindex), "launch"])

    def shutdown(self, vmindex: str) -> str:
        return self._run(["control", "--vmindex", str(vmindex), "shutdown"])

    def close_main(self) -> str:
        """优雅地关闭 MuMu 桌面程序。"""

        return self._run(["main", "close"])


def _mumu_controller(settings: dict[str, Any]) -> MuMuController:
    """按设置构建 CLI 控制器，并规范化命令超时时间。"""

    command_timeout = number_setting(
        settings, "mumu_command_timeout_seconds", 10.0, minimum=1.0
    )
    return MuMuController(mumu_cli_path(settings), command_timeout=command_timeout)


def mumu_adb_address(instance_info: dict[str, Any]) -> str | None:
    """返回 MuMu 上报的本地 ADB 转发地址。"""

    adb_port = instance_info.get("adb_port")
    adb_host = instance_info.get("adb_host_ip", "127.0.0.1")
    if not adb_port or not isinstance(adb_host, str):
        return None
    return f"{adb_host}:{adb_port}"


def mumu_adb_address_from_settings(settings: dict[str, Any]) -> str | None:
    """按设置解析运行中 MuMu 实例的 ADB 转发地址。

    实例未上报 ADB 地址时返回 ``None``。
    """

    controller = _mumu_controller(settings)
    instance_info = controller.instance_info(str(settings.get("mumu_vm_index", 0)))
    return mumu_adb_address(instance_info)


def connect_to_mumu(settings: dict[str, Any]) -> AdbClient:
    """按设置构建 AdbClient，连接运行中 MuMu 实例的 ADB 端口并选中该设备。

    ADB 路径未配置或实例未上报 ADB 地址时抛出 :class:`MuMuError`。
    """

    adb_path = settings.get("adb_path")
    if not isinstance(adb_path, str) or not adb_path:
        raise MuMuError("未配置 ADB 路径。")
    adb = AdbClient(
        Path(adb_path),
        command_timeout=number_setting(
            settings, "command_timeout_seconds", 10.0, minimum=1.0
        ),
    )
    controller = _mumu_controller(settings)
    instance_info = controller.instance_info(str(settings.get("mumu_vm_index", 0)))
    address = mumu_adb_address(instance_info)
    if not address:
        raise MuMuError("MuMu 未返回动态 ADB 地址")
    adb.connect(address)
    adb.select_device(address)
    return adb


def shutdown_mumu(
    settings: dict[str, Any],
    log_callback: LogCallback | None = None,
    *,
    sleep_function: Callable[[float], None] = time.sleep,
) -> bool:
    """关闭配置的 MuMu 实例，并确认它真正停止。

    MuMu CLI 在关闭请求被接受后立即返回。若把这次返回当作关闭完成，
    模拟器可能仍在运行，因此要先轮询实例状态再报告成功。
    """

    log = noop_log(log_callback)
    if not settings.get("close_mumu_after_run", False):
        return False

    vmindex = str(settings.get("mumu_vm_index", 0))
    try:
        controller = _mumu_controller(settings)
        command_timeout = number_setting(
            settings, "mumu_command_timeout_seconds", 10.0, minimum=1.0
        )
        poll_interval = number_setting(
            settings, "mumu_poll_interval_seconds", 3.0, minimum=0.1
        )
        log(f"任务结束，关闭 MuMu 实例 {vmindex}")
        controller.shutdown(vmindex)
        shutdown_attempts = 1
        deadline = time.monotonic() + command_timeout
        last_state = "未知"
        while True:
            try:
                info = controller.instance_info(vmindex)
                if not isinstance(info, dict) or "is_process_started" not in info:
                    raise MuMuError(f"MuMu 实例状态格式错误: {info}")
                process_started = bool(info["is_process_started"])
                state = (
                    f"process={process_started}, "
                    f"android={bool(info.get('is_android_started'))}"
                )
                if state != last_state:
                    log(f"关闭后 MuMu 状态: {state}")
                    last_state = state
                if not process_started:
                    log(f"已关闭 MuMu 实例 {vmindex}")
                    return True
                if shutdown_attempts < 2:
                    shutdown_attempts += 1
                    log(f"MuMu 实例仍在运行，再次发送关闭请求 ({shutdown_attempts}/2)")
                    controller.shutdown(vmindex)
                    continue
            except (MuMuError, OSError, TypeError, ValueError, AttributeError) as exc:
                log(f"关闭后读取 MuMu 状态失败，将重试: {exc}")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sleep_function(min(poll_interval, remaining))
        log(
            f"关闭 MuMu 实例 {vmindex} 超时，实例仍在运行 "
            f"（等待 {command_timeout:g}s，最后状态: {last_state}）"
        )
        return False
    except (MuMuError, OSError, TypeError, ValueError) as exc:
        log(f"关闭 MuMu 实例失败: {exc}")
        return False


def shutdown_mumu_app(
    settings: dict[str, Any],
    log_callback: LogCallback | None = None,
) -> bool:
    """在启用 ``close_mumu_app_after_run`` 时关闭 MuMu 桌面程序。

    与 :func:`shutdown_mumu` 相互独立：后者只关闭配置的模拟器实例，
    本函数则退出桌面程序。
    """

    log = noop_log(log_callback)
    if not settings.get("close_mumu_app_after_run", False):
        return False

    try:
        controller = _mumu_controller(settings)
        log("任务结束，退出 MuMu 软件程序")
        controller.close_main()
        log("已请求退出 MuMu 软件程序")
        return True
    except (MuMuError, OSError, TypeError, ValueError) as exc:
        log(f"退出 MuMu 软件程序失败: {exc}")
        return False


def prepare_device(
    adb: AdbClient,
    settings: dict[str, Any],
    log_callback: LogCallback | None = None,
    stop_event: Event | None = None,
) -> Device:
    log = noop_log(log_callback)

    def check_stop() -> None:
        if stop_event is not None and stop_event.is_set():
            raise MuMuStopRequested("用户请求停止设备准备")

    auto_start = settings.get("auto_start_mumu", True)
    check_stop()

    vmindex = str(settings.get("mumu_vm_index", 0))
    controller = _mumu_controller(settings)
    timeout = number_setting(settings, "mumu_start_timeout_seconds", 30.0, minimum=0.0)
    poll_interval = number_setting(settings, "mumu_poll_interval_seconds", 3.0)
    log(f"准备 MuMu 实例: vmindex={vmindex}, timeout={timeout:g}s")

    instance_info: dict[str, Any] = {}
    process_started = False
    android_started = False
    try:
        check_stop()
        instance_info = controller.instance_info(vmindex)
        process_started = bool(instance_info.get("is_process_started"))
        android_started = bool(instance_info.get("is_android_started"))
        log(
            "MuMu 初始状态: "
            f"process={process_started}, android={android_started}, "
            f"adb={mumu_adb_address(instance_info) or '未分配'}"
        )
    except (MuMuError, AttributeError) as exc:
        action = "将尝试启动" if auto_start else "自动启动已关闭"
        log(f"读取 MuMu 实例状态失败，{action}: {exc}")

    if not process_started and auto_start:
        check_stop()
        log(f"启动 MuMu 实例 {vmindex}: {mumu_cli_path(settings)}")
        controller.launch(vmindex)
        log(f"已请求启动 MuMu 实例 {vmindex}，等待实例信息刷新")
    elif not process_started:
        log(f"MuMu 实例 {vmindex} 未运行，自动启动已关闭，等待动态 ADB 地址")
    elif not android_started:
        log(f"MuMu 实例 {vmindex} 已启动但 Android 尚未就绪，等待启动完成")
    else:
        log(f"MuMu 实例 {vmindex} 已运行，确认 ADB 设备上线")

    deadline = time.monotonic() + max(0, timeout)
    last_state = "未发现"
    last_address: str | None = None
    last_connected: str | None = None
    while time.monotonic() < deadline:
        check_stop()
        try:
            instance_info = controller.instance_info(vmindex)
            check_stop()
            address = mumu_adb_address(instance_info)
            state = (
                f"process={bool(instance_info.get('is_process_started'))}, "
                f"android={bool(instance_info.get('is_android_started'))}, "
                f"adb={address or '未分配'}"
            )
            if state != last_state:
                log(f"MuMu 状态更新: {state}")
                last_state = state
            if address:
                last_address = address
                if address != last_connected:
                    try:
                        log(f"连接 MuMu ADB 转发端口: {address}")
                        adb.connect(address)
                        last_connected = address
                        check_stop()
                    except AdbError as exc:
                        log(f"ADB 端口暂不可用，将重试: {exc}")
        except (MuMuError, AttributeError) as exc:
            last_state = f"实例信息读取失败: {exc}"
            log(last_state)

        try:
            devices = adb.list_devices()
            check_stop()
        except AdbError as exc:
            devices = []
            last_state = f"ADB 设备列表读取失败: {exc}"
            log(last_state)

        selected: Device | None = None
        if last_address:
            selected = next(
                (
                    device
                    for device in devices
                    if device.serial == last_address and device.state == "device"
                ),
                None,
            )
        if selected is not None:
            adb.serial = selected.serial
            log(f"MuMu ADB 设备已上线: {selected.serial}")
            return selected

        if last_address is None:
            last_state = "MuMu 未返回 ADB 地址"
            log(last_state)
        if devices:
            last_state = ", ".join(
                f"{device.serial}: {device.state}" for device in devices
            )
        if stop_event is not None:
            stop_event.wait(max(0, poll_interval))
        else:
            time.sleep(max(0, poll_interval))

    check_stop()
    if last_address is None:
        raise AdbError(f"等待 MuMu 动态 ADB 地址超时，最后状态: {last_state}")
    raise AdbError(f"等待 MuMu ADB 设备超时: {last_address}，最后状态: {last_state}")


def connect_to_running_mumu(
    adb: AdbClient,
    settings: dict[str, Any],
    log_callback: LogCallback | None = None,
) -> Device:
    """把 ADB 连接到已在运行的 MuMu 实例，不包含启动流程。"""

    log = noop_log(log_callback)
    vmindex = str(settings.get("mumu_vm_index", 0))
    controller = _mumu_controller(settings)
    instance_info = controller.instance_info(vmindex)
    address = mumu_adb_address(instance_info)
    if not address:
        raise AdbError(f"MuMu 实例 {vmindex} 未返回动态 ADB 地址")
    log(f"连接 MuMu ADB 转发端口: {address}")
    adb.connect(address)
    devices = adb.list_devices()
    selected = next(
        (
            device
            for device in devices
            if device.serial == address and device.state == "device"
        ),
        None,
    )
    if selected is None:
        raise AdbError(f"MuMu ADB 设备未上线: {address}")
    adb.serial = selected.serial
    log(f"已连接调试设备: {selected.serial}")
    return selected
