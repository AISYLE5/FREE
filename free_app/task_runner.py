from __future__ import annotations

import time
from collections.abc import Callable
from threading import Event
from typing import Any

from .constants import MAX_TASK_EXECUTION_COUNT
from .models import RunResult, RunStatus, TaskDefinition


def task_execution_count(settings: dict[str, Any], task_id: str) -> int:
    """返回批量执行中一个任务的运行次数。

    ``load_settings`` 保证 ``task_execution_counts`` 是已钳制到
    ``[0, MAX_TASK_EXECUTION_COUNT]`` 的 ``dict[str, int]``；
    缺失条目表示任务运行一次。
    """

    counts = settings.get("task_execution_counts") or {}
    # int() 是消费点断言：契约外脏类型显式抛错，不静默透传。
    return int(counts.get(task_id, 1))


def batch_tasks_to_run(
    tasks: list[TaskDefinition] | tuple[TaskDefinition, ...],
    settings: dict[str, Any],
) -> list[TaskDefinition]:
    """返回“执行全部”批量执行时应运行的任务。

    执行次数为 0 的任务被跳过。每个返回的任务只出现一次；
    允许的尝试次数由 :func:`run_task_executions` 处理。
    """

    selected: list[TaskDefinition] = []
    for task in tasks:
        if task_execution_count(settings, task.id) > 0:
            selected.append(task)
    return selected


def run_task_executions(
    task: TaskDefinition,
    run_attempt: Callable[[], RunResult],
    execution_count: int,
    log_callback: Callable[[str], None],
    cleanup_callback: Callable[[], object] | None = None,
    reconnect_callback: Callable[[], object] | None = None,
    stop_event: Event | None = None,
) -> RunResult:
    """将任务运行最多 ``execution_count`` 次，成功即停止。

    一次尝试成功会立即结束运行；失败的尝试会重试，直到成功或
    次数用尽（次数 N 最多允许 N-1 次重试）。单任务调用方传入
    ``execution_count=1``，因此配置的执行次数不影响手动单次运行。
    每次重试前，可选的 ``reconnect_callback`` 在 ``cleanup_callback``
    之后执行，在下一次尝试前恢复已断开的网络 ADB 设备。
    """

    total_attempts = min(MAX_TASK_EXECUTION_COUNT, max(1, execution_count))
    for attempt in range(total_attempts):
        attempt_number = attempt + 1
        log_callback(
            f"任务尝试 [{attempt_number}/{total_attempts}] 开始: "
            f"{task.name} ({task.id})"
        )
        started = time.monotonic()
        result = run_attempt()
        error = f", error={result.error}" if result.error else ""
        log_callback(
            f"任务尝试 [{attempt_number}/{total_attempts}] 结束: "
            f"status={result.status.value}, "
            f"completed={result.completed_steps}/{result.total_steps}, "
            f"elapsed={time.monotonic() - started:.2f}s{error}"
        )
        if result.status != RunStatus.FAILED or attempt >= total_attempts - 1:
            return result
        if stop_event is not None and stop_event.is_set():
            return result

        next_attempt = attempt + 2
        retry_note = f": {result.error}" if result.error else ""
        log_callback(
            f"任务失败，将重新尝试 [{next_attempt}/{total_attempts}] {task.name}{retry_note}"
        )
        if cleanup_callback is not None:
            try:
                cleanup_callback()
            except Exception as exc:
                log_callback(f"重试前 App 清理失败: {exc}")
        if stop_event is not None and stop_event.is_set():
            return result
        if reconnect_callback is not None:
            try:
                reconnect_callback()
            except Exception as exc:
                log_callback(f"重试前 ADB 重连失败: {exc}")
        if stop_event is not None and stop_event.is_set():
            return result

    # 不变式：每次迭代要么返回，要么向 total_attempts 推进。
    raise RuntimeError("任务执行流程异常结束")
