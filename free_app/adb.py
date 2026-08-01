from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Literal, Sequence, overload

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
            # Logging must never turn a successful device operation into a
            # failed automation action.
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
            "check": False,
            "creationflags": subprocess.CREATE_NO_WINDOW,
        }
        if not binary:
            run_options.update(text=True, encoding="utf-8", errors="replace")
        try:
            completed = subprocess.run(command, **run_options)
        except subprocess.TimeoutExpired as exc:
            if binary:
                # Binary callers (e.g. screenshot) own their command-specific
                # timeout messaging, so surface the raw exception for re-wrapping.
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
        size_matches = re.findall(r"(?:Physical|Override) size:\s*(\d+)x(\d+)", size_output)
        density_matches = re.findall(r"(?:Physical|Override) density:\s*(\d+)", density_output)
        if not size_matches or not density_matches:
            raise AdbError(f"无法解析模拟器规格: size={size_output!r}, density={density_output!r}")
        width, height = map(int, size_matches[-1])
        density = int(density_matches[-1])
        return ScreenInfo(width, height, density)

    def select_device(self, preferred_serial: str | None = None) -> Device:
        devices = self.list_devices()
        ready = [device for device in devices if device.state == "device"]
        preferred = preferred_serial or self.serial
        if not preferred:
            raise AdbError("未指定目标 ADB serial，禁止自动选择任意 ADB 设备")
        selected = next((device for device in ready if device.serial == preferred), None)
        if selected:
            self.serial = selected.serial
            return selected
        matching = next((device for device in devices if device.serial == preferred), None)
        if matching:
            raise AdbError(f"设备 {preferred} 当前状态为 {matching.state}")
        states = ", ".join(f"{device.serial}: {device.state}" for device in devices) or "没有设备"
        raise AdbError(f"找不到目标 ADB 设备 {preferred}（当前设备: {states}）")

    def shell(self, *arguments: str, check: bool = True) -> str:
        return self._run(self._device_args() + ["shell", *arguments], check=check)

    def connect(self, address: str) -> str:
        """Connect the local ADB client to a MuMu instance's forwarded port."""

        return self._run(["connect", address])

    def reconnect(self) -> bool:
        """Try to bring the selected network ADB device back online.

        Local emulator names such as ``emulator-5556`` are intentionally
        skipped because they cannot be re-added through ``adb connect``.
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
        # Explicitly target the launcher activity. Plain "monkey -p <pkg> 1"
        # only sends random events and may return without opening the app.
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
        self.shell("input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms))

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
        # MuMu can write a valid dump and still exit uiautomator with code 139.
        # The XML read is the reliable success signal, not the dump process code.
        self.shell("rm", "-f", remote_path, check=False)
        dump_error: AdbError | None = None
        try:
            self.shell("uiautomator", "dump", remote_path)
        except AdbError as exc:
            dump_error = exc
        try:
            xml = self.exec_out("cat", remote_path)
        except AdbError:
            if dump_error is not None:
                raise AdbError(f"UI dump 命令失败: {dump_error}") from dump_error
            raise
        if "<hierarchy" not in xml:
            detail = f": {dump_error}" if dump_error is not None else ""
            raise AdbError(f"UI dump 没有返回有效的 hierarchy XML{detail}")
        return xml

    def current_package(self) -> str | None:
        outputs = (
            self.shell("dumpsys", "window", "windows", check=False),
            self.shell("dumpsys", "activity", "activities", check=False),
        )
        markers = ("mCurrentFocus", "mFocusedApp", "topResumedActivity", "ResumedActivity")
        for output in outputs:
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
