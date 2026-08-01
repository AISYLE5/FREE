from __future__ import annotations

import time
from threading import Event
from typing import Any, Callable

from .constants import MAX_TASK_EXECUTION_COUNT
from .models import RunResult, RunStatus, TaskDefinition


def task_execution_count(
    settings: dict[str, Any],
    default: int = 1,
    *,
    task_id: str | None = None,
) -> int:
    """Return the number of times a task runs during a batch execution."""

    configured_counts = settings.get("task_execution_counts")
    if not isinstance(configured_counts, dict):
        configured_counts = {}
    configured = (
        configured_counts.get(task_id)
        if task_id
        else default
    )
    try:
        value = int(float(configured)) if configured is not None else default
    except (TypeError, ValueError):
        value = default
    return min(MAX_TASK_EXECUTION_COUNT, max(0, value))


def batch_tasks_to_run(
    tasks: list[TaskDefinition] | tuple[TaskDefinition, ...],
    settings: dict[str, Any],
) -> list[TaskDefinition]:
    """Return tasks that should run during ``执行全部``.

    Tasks whose execution count is 0 are skipped.  Each returned task appears
    once; the number of allowed attempts is handled by
    :func:`run_task_executions`.
    """

    selected: list[TaskDefinition] = []
    for task in tasks:
        count = task_execution_count(settings, task_id=task.id)
        if count > 0:
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
    """Run a task up to ``execution_count`` times, stopping on success.

    A successful first attempt moves on immediately.  A failed attempt is
    retried until it succeeds or the configured execution count is reached
    (so count N allows at most N-1 retries).  Single-task callers pass
    ``execution_count=1`` so the setting never affects a manual single run.
    Before each retry the optional ``reconnect_callback`` runs after cleanup
    so a disconnected network ADB device can be restored first.
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

    # Invariant: every iteration either returns or advances toward total_attempts.
    raise RuntimeError("任务执行流程异常结束")
