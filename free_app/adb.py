from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, overload

from .helpers import LogCallback


class AdbError(RuntimeError):
    pass


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    details: str = ""


@dataclass(frozen=True)
class ScreenInfo:
    width: int
    height: int
    density: int


class AdbClient:
    def __init__(
        self,
        executable: Path,
        serial: str | None = None,
        command_timeout: float = 15.0,
        log_callback: LogCallback | None = None,
    ):
        self.executable = Path(executable)
        self.serial = serial
        self.command_timeout = command_timeout
        self.log_callback = log_callback

    @staticmethod
    def _preview(value: str, limit: int = 240) -> str:
        compact = " ".join(value.split())
        if len(compact) > limit:
            return compact[:limit] + "…"
        return compact

    def _trace(self, message: str) -> None:
        if self.log_callback is None:
            return
        try:
            self.log_callback(message)
        except Exception:
            # 日志记录绝不能把一次成功的设备操作变成
            # 失败的自动化动作。
            return

    @overload
    def _run(
        self,
        arguments: Sequence[str],
        timeout: float | None = None,
        check: bool = True,
        binary: Literal[False] = ...,
    ) -> str: ...

    @overload
    def _run(
        self,
        arguments: Sequence[str],
        timeout: float | None = None,
        check: bool = True,
        binary: Literal[True] = ...,
    ) -> subprocess.CompletedProcess: ...

    def _run(
        self,
        arguments: Sequence[str],
        timeout: float | None = None,
        check: bool = True,
        binary: bool = False,
    ) -> str | subprocess.CompletedProcess:
        if not self.executable.exists():
            raise AdbError(f"找不到 ADB: {self.executable}")
        command = [str(self.executable)] + list(arguments)
        command_text = subprocess.list2cmdline(command)
        effective_timeout = timeout or self.command_timeout
        started = time.monotonic()
        run_options: dict[str, Any] = {
            "capture_output": True,
            "timeout": effective_timeout,
            "creationflags": subprocess.CREATE_NO_WINDOW,
        }
        if not binary:
            run_options.update(text=True, encoding="utf-8", errors="replace")
        try:
            completed = subprocess.run(command, check=False, **run_options)
        except subprocess.TimeoutExpired as exc:
            if binary:
                # 二进制调用方（如截图）自行处理命令专属的
                # 超时提示，因此这里直接抛出原始异常交由上层重新包装。
                raise
            elapsed = time.monotonic() - started
            self._trace(
                f"ADB 命令超时: command={command_text}, timeout={effective_timeout:g}s, "
                f"elapsed={elapsed:.2f}s"
            )
            raise AdbError(f"ADB 命令超时: {command_text}") from exc
        if binary:
            return completed
        output = (completed.stdout or "").strip()
        error = (completed.stderr or "").strip()
        elapsed = time.monotonic() - started
        if check and completed.returncode != 0:
            detail = error or output or f"exit code {completed.returncode}"
            self._trace(
                f"ADB 命令失败: command={command_text}, exit_code={completed.returncode}, "
                f"elapsed={elapsed:.2f}s, detail={self._preview(detail)}"
            )
            raise AdbError(f"ADB 命令失败: {detail}")
        preview = f", output={self._preview(output)}" if output else ""
        self._trace(
            f"ADB 命令完成: command={command_text}, exit_code={completed.returncode}, "
            f"elapsed={elapsed:.2f}s, output_chars={len(output)}{preview}"
        )
        return output

    def _device_args(self) -> list[str]:
        if not self.serial:
            return []
        return ["-s", self.serial]

    def list_devices(self) -> list[Device]:
        output = self._run(["devices", "-l"])
        devices: list[Device] = []
        for line in output.splitlines()[1:]:
            fields = line.split(maxsplit=2)
            if (
                len(fields) >= 2
                and fields[0]
                and fields[1] in {"device", "offline", "unauthorized", "bootloader"}
            ):
                devices.append(
                    Device(fields[0], fields[1], fields[2] if len(fields) == 3 else "")
                )
        return devices

    def screen_info(self) -> ScreenInfo:
        size_output = self.shell("wm", "size")
        density_output = self.shell("wm", "density")
        size_matches = re.findall(
            r"(?:Physical|Override) size:\s*(\d+)x(\d+)", size_output
        )
        density_matches = re.findall(
            r"(?:Physical|Override) density:\s*(\d+)", density_output
        )
        if not size_matches or not density_matches:
            raise AdbError(
                f"无法解析模拟器规格: size={size_output!r}, density={density_output!r}"
            )
        width, height = map(int, size_matches[-1])
        density = int(density_matches[-1])
        return ScreenInfo(width, height, density)

    def select_device(self, preferred_serial: str | None = None) -> Device:
        devices = self.list_devices()
        ready = [device for device in devices if device.state == "device"]
        preferred = preferred_serial or self.serial
        if not preferred:
            raise AdbError("未指定目标 ADB serial，禁止自动选择任意 ADB 设备")
        selected = next(
            (device for device in ready if device.serial == preferred), None
        )
        if selected:
            self.serial = selected.serial
            return selected
        matching = next(
            (device for device in devices if device.serial == preferred), None
        )
        if matching:
            raise AdbError(f"设备 {preferred} 当前状态为 {matching.state}")
        states = (
            ", ".join(f"{device.serial}: {device.state}" for device in devices)
            or "没有设备"
        )
        raise AdbError(f"找不到目标 ADB 设备 {preferred}（当前设备: {states}）")

    def shell(self, *arguments: str, check: bool = True) -> str:
        return self._run(self._device_args() + ["shell", *arguments], check=check)

    def connect(self, address: str) -> str:
        """将本地 ADB 客户端连接到 MuMu 实例的转发端口。"""

        return self._run(["connect", address])

    def reconnect(self) -> bool:
        """尝试让选中的网络 ADB 设备重新上线。

        ``emulator-5556`` 这类本地模拟器名称会被有意跳过，
        因为它们无法通过 ``adb connect`` 重新添加。
        """

        if not self.serial or ":" not in self.serial:
            return False
        try:
            self.connect(self.serial)
            self.select_device(self.serial)
            return True
        except AdbError:
            return False

    def exec_out(self, *arguments: str) -> str:
        return self._run(self._device_args() + ["exec-out", *arguments])

    def force_stop(self, package: str) -> None:
        self.shell("am", "force-stop", package)

    def launch(self, package: str) -> None:
        # 显式指定启动器 Activity。裸的 "monkey -p <pkg> 1"
        # 只会发送随机事件，可能不打开应用就直接返回。
        self.shell(
            "monkey",
            "-p",
            package,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        )

    def press_back(self) -> None:
        self.shell("input", "keyevent", "KEYCODE_BACK")

    def tap(self, x: int, y: int) -> None:
        self.shell("input", "tap", str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.shell(
            "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)
        )

    def screenshot(self) -> bytes:
        if not self.serial:
            raise AdbError("尚未选择 ADB 设备")
        arguments = self._device_args() + ["exec-out", "screencap", "-p"]
        command_text = subprocess.list2cmdline([str(self.executable), *arguments])
        started = time.monotonic()
        try:
            completed = self._run(arguments, binary=True)
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - started
            self._trace(
                f"ADB 截图命令超时: command={command_text}, timeout={self.command_timeout:g}s, "
                f"elapsed={elapsed:.2f}s"
            )
            raise AdbError("截图命令超时") from exc
        elapsed = time.monotonic() - started
        if completed.returncode != 0:
            error = (
                completed.stderr.decode("utf-8", errors="replace")
                if completed.stderr
                else "截图失败"
            )
            self._trace(
                f"ADB 截图命令失败: command={command_text}, exit_code={completed.returncode}, "
                f"elapsed={elapsed:.2f}s, detail={self._preview(error)}"
            )
            raise AdbError(error)
        self._trace(
            f"ADB 截图命令完成: command={command_text}, exit_code=0, elapsed={elapsed:.2f}s, "
            f"bytes={len(completed.stdout)}"
        )
        return completed.stdout

    def dump_ui(self) -> str:
        remote_path = "/sdcard/free_window_dump.xml"
        # MuMu 可能已写出有效的界面 dump，但 uiautomator 仍以退出码 139 结束，
        # 因此吞掉 dump 自身的输出与退出码，以 cat 的实际读取结果为准。
        # 单条 sh -c 把原来的 3 个子进程（rm + dump + cat）压成 1 个——
        # 这是 detect/click 轮询的最热路径。
        script = (
            f"rm -f {remote_path}; "
            f"uiautomator dump {remote_path} >/dev/null 2>&1; "
            f"cat {remote_path}"
        )
        xml = self.shell("sh", "-c", script, check=False)
        if "<hierarchy" not in xml:
            raise AdbError("UI dump 没有返回有效的 hierarchy XML")
        return xml

    def current_package(self) -> str | None:
        """返回前台应用包名，首次命中即短路返回。

        窗口 dump 通常已能给出焦点应用；只有第一条命令查不到时才读取
        activity dump，让引擎每次动作的前台检查保持轻量。
        """

        for arguments in (
            ("dumpsys", "window", "windows"),
            ("dumpsys", "activity", "activities"),
        ):
            output = self.shell(*arguments, check=False)
            package = self._package_from_dumpsys(output)
            if package is not None:
                return package
        return None

    @staticmethod
    def _package_from_dumpsys(output: str) -> str | None:
        markers = (
            "mCurrentFocus",
            "mFocusedApp",
            "topResumedActivity",
            "ResumedActivity",
        )
        for line in output.splitlines():
            if not any(marker in line for marker in markers):
                continue
            parts = line.replace("}", " ").split()
            for part in parts:
                if "/" not in part:
                    continue
                package = part.split("/", 1)[0].strip("{")
                if (
                    package
                    and "." in package
                    and all(
                        character.isalnum() or character in "_.$"
                        for character in package
                    )
                ):
                    return package
        return None
