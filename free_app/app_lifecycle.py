from __future__ import annotations

import time
from typing import Any, Callable

from .adb import AdbClient
from .helpers import LogCallback, noop_log, number_setting


def cleanup_apps(
    adb: AdbClient,
    settings: dict[str, Any],
    log_callback: LogCallback | None = None,
    sleep_function: Callable[[float], None] = time.sleep,
) -> None:
    log = noop_log(log_callback)
    cleanup_value = settings.get("cleanup_after_task", True)
    # *None*/*""* mean "enabled", never "disabled"; shipped settings may store either.
    if cleanup_value is None or cleanup_value == "":
        cleanup_value = True
    if not bool(cleanup_value):
        log("任务结束清理跳过：cleanup_after_task=False")
        return

    raw_packages = settings.get("cleanup_packages", [])
    if not isinstance(raw_packages, list):
        log("任务结束清理跳过：cleanup_packages 不是列表")
        return
    packages = list(
        dict.fromkeys(
            str(package).strip() for package in raw_packages if str(package).strip()
        )
    )
    if not packages:
        log("任务结束清理跳过：没有配置 App 包名")
        return

    delay_seconds = number_setting(settings, "cleanup_delay_seconds", 3.0, minimum=0)
    if delay_seconds:
        log(f"任务结束，{delay_seconds:g} 秒后开始关闭 App 进程")
        sleep_function(delay_seconds)
    log(f"开始关闭 App 进程: {', '.join(packages)}")
    for package in packages:
        try:
            adb.force_stop(package)
        except Exception as exc:
            log(f"关闭 App 进程失败: {package}: {exc}")
        else:
            log(f"已关闭 App 进程: {package}")
