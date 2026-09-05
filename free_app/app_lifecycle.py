from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

from .adb import AdbClient
from .helpers import LogCallback, noop_log, number_setting


def collect_packages(tasks: Iterable[Any]) -> list[str]:
    """收集任务引用的所有包名（任务级与动作级）。

    递归遍历任务定义（包括嵌套的 if/loop/复合步骤），
    按首次出现的顺序返回去重后的包名。
    """

    packages: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            package = value.get("package")
            if isinstance(package, str) and package.strip():
                packages.append(package.strip())
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
        elif hasattr(value, "parameters"):
            visit(value.parameters)

    for task in tasks:
        task_package = getattr(task, "package", None)
        if isinstance(task_package, str) and task_package.strip():
            packages.append(task_package.strip())
        visit(getattr(task, "actions", None))
    return list(dict.fromkeys(packages))


def cleanup_apps(
    adb: AdbClient,
    settings: dict[str, Any],
    tasks: Iterable[Any],
    log_callback: LogCallback | None = None,
    sleep_function: Callable[[float], None] = time.sleep,
) -> None:
    log = noop_log(log_callback)
    # 设置清洗层保证 cleanup_after_task 为布尔；此处直接信任。
    if not settings.get("cleanup_after_task", True):
        log("任务结束清理跳过：cleanup_after_task=False")
        return

    packages = collect_packages(tasks)
    if not packages:
        log("任务结束清理跳过：任务里没有配置包名")
        return

    delay_seconds = number_setting(settings, "cleanup_delay_seconds", 5.0, minimum=0)
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
