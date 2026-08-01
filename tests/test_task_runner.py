from __future__ import annotations

import unittest
from threading import Event
from unittest.mock import MagicMock

from free_app.models import Action, RunResult, RunStatus, TaskDefinition
from free_app.task_runner import batch_tasks_to_run, run_task_executions, task_execution_count


def make_task() -> TaskDefinition:
    return TaskDefinition(
        id="demo",
        name="Demo",
        package="demo.package",
        actions=(Action("wait", {"seconds": 0}),),
    )


def make_result(status: RunStatus, error: str | None = None) -> RunResult:
    return RunResult("demo", status, 0, 1, error=error)


class TaskRunnerTests(unittest.TestCase):
    def test_task_execution_count_uses_per_task_values_with_bounds(self) -> None:
        self.assertEqual(
            task_execution_count({"task_execution_counts": {"demo": "invalid"}}, task_id="demo"),
            1,
        )
        self.assertEqual(
            task_execution_count({"task_execution_counts": {"demo": -2}}, task_id="demo"),
            0,
        )
        self.assertEqual(
            task_execution_count({"task_execution_counts": {"demo": 99}}, task_id="demo"),
            10,
        )
        self.assertEqual(
            task_execution_count({"task_execution_counts": {"demo": "4.9"}}, task_id="demo"),
            4,
        )
        self.assertEqual(task_execution_count({"task_execution_counts": {}}, task_id="demo"), 1)

    def test_task_execution_count_ignores_unknown_counts_key(self) -> None:
        settings = {
            "task_execution_counts": {"demo": 2},
            "stale_counts": {"demo": 5},
        }

        self.assertEqual(task_execution_count(settings, task_id="demo"), 2)
        self.assertEqual(
            task_execution_count({"stale_counts": {"demo": 5}}, task_id="demo"),
            1,
        )
        self.assertEqual(task_execution_count(settings, task_id="other"), 1)

    def test_batch_tasks_to_run_skips_zero_and_keeps_unique_tasks(self) -> None:
        first = make_task()
        second = TaskDefinition(
            id="second",
            name="Second",
            package="second.package",
            actions=(Action("wait", {"seconds": 0}),),
        )
        selected = batch_tasks_to_run(
            [first, second],
            {"task_execution_counts": {"demo": 0, "second": 3}},
        )
        self.assertEqual([task.id for task in selected], ["second"])

    def test_run_task_executions_stops_after_first_success(self) -> None:
        run_attempt = MagicMock(return_value=make_result(RunStatus.SUCCESS))
        cleanup = MagicMock()

        result = run_task_executions(
            make_task(),
            run_attempt,
            3,
            MagicMock(),
            cleanup_callback=cleanup,
        )

        self.assertEqual(result.status, RunStatus.SUCCESS)
        run_attempt.assert_called_once()
        cleanup.assert_not_called()

    def test_run_task_executions_retries_failed_attempt_up_to_count(self) -> None:
        run_attempt = MagicMock(
            side_effect=[
                make_result(RunStatus.FAILED, "first failure"),
                make_result(RunStatus.SUCCESS),
            ]
        )
        cleanup = MagicMock()

        result = run_task_executions(
            make_task(),
            run_attempt,
            3,
            MagicMock(),
            cleanup_callback=cleanup,
        )

        self.assertEqual(result.status, RunStatus.SUCCESS)
        self.assertEqual(run_attempt.call_count, 2)
        cleanup.assert_called_once()

    def test_run_task_executions_returns_failure_after_all_attempts(self) -> None:
        run_attempt = MagicMock(return_value=make_result(RunStatus.FAILED, "always fails"))
        cleanup = MagicMock()

        result = run_task_executions(
            make_task(),
            run_attempt,
            2,
            MagicMock(),
            cleanup_callback=cleanup,
        )

        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(run_attempt.call_count, 2)
        cleanup.assert_called_once()

    def test_run_task_executions_with_count_one_never_retries(self) -> None:
        run_attempt = MagicMock(return_value=make_result(RunStatus.FAILED, "failed"))
        cleanup = MagicMock()

        result = run_task_executions(
            make_task(),
            run_attempt,
            1,
            MagicMock(),
            cleanup_callback=cleanup,
        )

        self.assertEqual(result.status, RunStatus.FAILED)
        run_attempt.assert_called_once()
        cleanup.assert_not_called()

    def test_run_task_executions_stop_event_prevents_retry(self) -> None:
        stop_event = Event()
        run_attempt = MagicMock(
            side_effect=lambda: (
                stop_event.set(),
                make_result(RunStatus.FAILED, "failed"),
            )[1]
        )
        cleanup = MagicMock()

        result = run_task_executions(
            make_task(),
            run_attempt,
            3,
            MagicMock(),
            cleanup_callback=cleanup,
            stop_event=stop_event,
        )

        self.assertEqual(result.status, RunStatus.FAILED)
        run_attempt.assert_called_once()
        cleanup.assert_not_called()

    def test_run_task_executions_stops_after_cleanup_sets_event(self) -> None:
        stop_event = Event()
        run_attempt = MagicMock(return_value=make_result(RunStatus.FAILED, "failed"))
        cleanup = MagicMock(side_effect=stop_event.set)

        result = run_task_executions(
            make_task(),
            run_attempt,
            3,
            MagicMock(),
            cleanup_callback=cleanup,
            stop_event=stop_event,
        )

        self.assertEqual(result.status, RunStatus.FAILED)
        run_attempt.assert_called_once()
        cleanup.assert_called_once()

    def test_run_task_executions_ignores_cleanup_failure_and_retries(self) -> None:
        run_attempt = MagicMock(
            side_effect=[
                make_result(RunStatus.FAILED, "first failure"),
                make_result(RunStatus.SUCCESS),
            ]
        )
        cleanup = MagicMock(side_effect=RuntimeError("cleanup crashed"))

        result = run_task_executions(
            make_task(),
            run_attempt,
            3,
            MagicMock(),
            cleanup_callback=cleanup,
        )

        self.assertEqual(result.status, RunStatus.SUCCESS)
        self.assertEqual(run_attempt.call_count, 2)
        cleanup.assert_called_once()

    def test_run_task_executions_reconnects_before_retry(self) -> None:
        run_attempt = MagicMock(
            side_effect=[
                make_result(RunStatus.FAILED, "first failure"),
                make_result(RunStatus.SUCCESS),
            ]
        )
        cleanup = MagicMock()
        reconnect = MagicMock()

        result = run_task_executions(
            make_task(),
            run_attempt,
            3,
            MagicMock(),
            cleanup_callback=cleanup,
            reconnect_callback=reconnect,
        )

        self.assertEqual(result.status, RunStatus.SUCCESS)
        reconnect.assert_called_once()

    def test_run_task_executions_continues_when_reconnect_fails(self) -> None:
        run_attempt = MagicMock(
            side_effect=[
                make_result(RunStatus.FAILED, "first failure"),
                make_result(RunStatus.SUCCESS),
            ]
        )
        reconnect = MagicMock(side_effect=RuntimeError("reconnect crashed"))

        result = run_task_executions(
            make_task(),
            run_attempt,
            3,
            MagicMock(),
            reconnect_callback=reconnect,
        )

        self.assertEqual(result.status, RunStatus.SUCCESS)
        self.assertEqual(run_attempt.call_count, 2)


if __name__ == "__main__":
    unittest.main()
