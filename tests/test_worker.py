from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from free_app.adb import AdbError
from free_app.config import TaskFileError, load_task_directory
from free_app.models import Action, RunResult, RunStatus, TaskDefinition
from free_app.mumu import MuMuStopRequested
from free_app.worker import BatchTaskWorker, TaskWorker, _prune_outputs, reconnect_device


class FakeAdb:
    serial = "127.0.0.1:16416"

    def __init__(self) -> None:
        self.taps: list[tuple[int, int]] = []

    def select_device(self, _preferred: str | None = None) -> object:
        from free_app.adb import Device

        return Device("127.0.0.1:16416", "device")

    def dump_ui(self) -> str:
        return (
            '<hierarchy><node text="领取" clickable="true" enabled="true" '
            'visible-to-user="true" bounds="[10,20][110,80]" /></hierarchy>'
        )

    def tap(self, x: int, y: int) -> None:
        self.taps.append((x, y))


def make_task(task_id: str = "demo") -> TaskDefinition:
    return TaskDefinition(
        id=task_id,
        name="示例任务",
        package="demo.package",
        actions=(Action("click_text", {"text": "领取", "timeout_seconds": 0}),),
    )


class WorkerTests(unittest.TestCase):
    def test_prune_outputs_limits_screenshots_by_max_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            logs = base / "logs"
            screenshots = base / "screenshots"
            logs.mkdir()
            screenshots.mkdir()
            (logs / "run.log").write_text("x", encoding="utf-8")
            for index in range(3):
                (screenshots / f"{index}.png").write_text("x", encoding="utf-8")
            settings = {
                "log_directory": "logs",
                "screenshot_directory": "screenshots",
                "max_log_files": -1,
                "max_screenshot_files": 1,
                "cleanup_mode": "permanent",
            }

            _prune_outputs(settings, base, lambda _message: None)

            remaining = sorted(path.name for path in screenshots.glob("*.png"))
            self.assertEqual(remaining, ["2.png"])
            self.assertEqual(len(list(logs.glob("*.log"))), 1)

    def test_reconnect_device_uses_adb_reconnect_and_logs_success(self) -> None:
        adb = FakeAdb()
        adb.reconnect = MagicMock(return_value=True)  # type: ignore[attr-defined]
        logs: list[str] = []

        self.assertTrue(reconnect_device(adb, logs.append))
        adb.reconnect.assert_called_once()
        self.assertIn("ADB 设备已重新连接", logs[0])

    def test_single_worker_runs_once_even_with_high_execution_count(self) -> None:
        task = make_task()
        worker = TaskWorker(
            task,
            FakeAdb(),
            Path("screenshots"),
            0,
            settings={"task_execution_counts": {task.id: 3}},
        )
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with patch(
            "free_app.worker.prepare_device",
            return_value=True,
        ), patch("free_app.worker.shutdown_mumu", return_value=True), patch(
            "free_app.worker.cleanup_apps"
        ), patch("free_app.worker.send_run_notification") as notify, patch.object(
            worker.engine,
            "run",
            return_value=RunResult(task.id, RunStatus.SUCCESS, 1, 1),
        ) as run:
            worker.run()

        self.assertEqual(run.call_count, 1)
        self.assertEqual(finished[0].status, RunStatus.SUCCESS)
        notify.assert_called_once()

    def test_single_worker_converts_engine_exception_to_failed_result(self) -> None:
        worker = TaskWorker(
            make_task(),
            FakeAdb(),
            Path("screenshots"),
            0,
        )
        with patch.object(worker.engine, "run", side_effect=RuntimeError("engine crashed")):
            result = worker._run_attempt()

        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.failed_step, "执行任务")
        self.assertIn("engine crashed", result.error or "")

    def test_debug_worker_skips_cleanup_and_notification(self) -> None:
        task = make_task()
        worker = TaskWorker(
            task,
            FakeAdb(),
            Path("screenshots"),
            0,
            debug=True,
        )
        with patch("free_app.worker.prepare_device") as prepare, patch(
            "free_app.worker.connect_to_running_mumu",
            return_value=True,
        ) as connect, patch(
            "free_app.worker.cleanup_apps"
        ) as cleanup, patch("free_app.worker.shutdown_mumu") as shutdown, patch(
            "free_app.worker.shutdown_mumu_app"
        ) as shutdown_app, patch(
            "free_app.worker.send_run_notification"
        ) as notify, patch.object(
            worker.engine,
            "run",
            return_value=RunResult(task.id, RunStatus.SUCCESS, 1, 1),
        ):
            worker.run()

        connect.assert_called_once()
        prepare.assert_not_called()
        cleanup.assert_not_called()
        shutdown.assert_not_called()
        shutdown_app.assert_not_called()
        notify.assert_not_called()

    def test_single_worker_executes_once_when_execution_count_is_zero(self) -> None:
        task = make_task()
        worker = TaskWorker(
            task,
            FakeAdb(),
            Path("screenshots"),
            0,
            settings={"task_execution_counts": {task.id: 0}},
        )
        with patch("free_app.worker.prepare_device", return_value=True), patch(
            "free_app.worker.shutdown_mumu", return_value=True
        ), patch("free_app.worker.cleanup_apps"), patch(
            "free_app.worker.send_run_notification"
        ), patch.object(
            worker.engine,
            "run",
            return_value=RunResult(task.id, RunStatus.FAILED, 0, 1, error="失败"),
        ) as run:
            worker.run()

        run.assert_called_once()

    def test_batch_worker_retries_failed_task_up_to_execution_count(self) -> None:
        task = make_task()
        worker = BatchTaskWorker(
            [task],
            FakeAdb(),
            Path("screenshots"),
            0,
            settings={"task_execution_counts": {task.id: 2}},
        )
        first_engine = MagicMock()
        first_engine.run.return_value = RunResult(task.id, RunStatus.FAILED, 0, 1, error="第一次失败")
        second_engine = MagicMock()
        second_engine.run.return_value = RunResult(task.id, RunStatus.SUCCESS, 1, 1)
        finished: list[object] = []
        task_finished: list[object] = []
        worker.finished.connect(finished.append)
        worker.task_finished.connect(task_finished.append)
        with patch("free_app.worker.prepare_device", return_value=True), patch(
            "free_app.worker.shutdown_mumu", return_value=True
        ), patch("free_app.worker.cleanup_apps"), patch(
            "free_app.worker.send_run_notification"
        ), patch.object(worker, "_make_engine", side_effect=[first_engine, second_engine]):
            worker.run()

        self.assertEqual(first_engine.run.call_count, 1)
        self.assertEqual(second_engine.run.call_count, 1)
        self.assertEqual(len(task_finished), 1)
        self.assertEqual(finished[0].status, RunStatus.SUCCESS)
        self.assertEqual(finished[0].completed_tasks, 1)
        self.assertEqual(finished[0].total_tasks, 1)

    def test_batch_worker_reconnects_adb_before_retry_attempt(self) -> None:
        class ReconnectAdb(FakeAdb):
            def __init__(self) -> None:
                super().__init__()
                self.reconnect_count = 0

            def reconnect(self) -> bool:
                self.reconnect_count += 1
                return True

        task = make_task()
        adb = ReconnectAdb()
        worker = BatchTaskWorker(
            [task],
            adb,
            Path("screenshots"),
            0,
            settings={"task_execution_counts": {task.id: 2}},
        )
        first_engine = MagicMock()
        first_engine.run.return_value = RunResult(task.id, RunStatus.FAILED, 0, 1, error="第一次失败")
        second_engine = MagicMock()
        second_engine.run.return_value = RunResult(task.id, RunStatus.SUCCESS, 1, 1)
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with patch("free_app.worker.prepare_device", return_value=True), patch(
            "free_app.worker.shutdown_mumu", return_value=True
        ), patch("free_app.worker.cleanup_apps"), patch(
            "free_app.worker.send_run_notification"
        ), patch.object(worker, "_make_engine", side_effect=[first_engine, second_engine]):
            worker.run()

        self.assertEqual(adb.reconnect_count, 1)
        self.assertEqual(finished[0].status, RunStatus.SUCCESS)

    def test_batch_worker_stops_after_first_success_when_count_is_higher(self) -> None:
        task = make_task()
        worker = BatchTaskWorker(
            [task],
            FakeAdb(),
            Path("screenshots"),
            0,
            settings={"task_execution_counts": {task.id: 3}},
        )
        engine = MagicMock()
        engine.run.return_value = RunResult(task.id, RunStatus.SUCCESS, 1, 1)
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with patch("free_app.worker.prepare_device", return_value=True), patch(
            "free_app.worker.shutdown_mumu", return_value=True
        ), patch("free_app.worker.cleanup_apps"), patch(
            "free_app.worker.send_run_notification"
        ), patch.object(worker, "_make_engine", return_value=engine):
            worker.run()

        engine.run.assert_called_once()
        self.assertEqual(finished[0].status, RunStatus.SUCCESS)

    def test_batch_stop_requests_current_engine_and_skips_remaining_tasks(self) -> None:
        first_task = make_task("first")
        second_task = make_task("second")
        worker = BatchTaskWorker(
            [first_task, second_task],
            FakeAdb(),
            Path("screenshots"),
            0,
        )
        engine = MagicMock()

        def stop_during_run(_task: TaskDefinition) -> RunResult:
            worker.stop()
            return RunResult(first_task.id, RunStatus.STOPPED, 0, 1)

        engine.run.side_effect = stop_during_run
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with patch("free_app.worker.prepare_device", return_value=True), patch(
            "free_app.worker.shutdown_mumu", return_value=True
        ), patch("free_app.worker.cleanup_apps"), patch(
            "free_app.worker.send_run_notification"
        ), patch.object(worker, "_make_engine", return_value=engine) as make_engine:
            worker.run()

        engine.request_stop.assert_called_once()
        make_engine.assert_called_once_with(first_task.id)
        summary = finished[0]
        self.assertEqual(summary.status, RunStatus.STOPPED)
        self.assertEqual(summary.completed_tasks, 1)
        self.assertEqual([result.task_id for result in summary.results], [first_task.id])

    def test_batch_engine_creation_failure_does_not_skip_later_tasks(self) -> None:
        first_task = make_task("first")
        second_task = make_task("second")
        worker = BatchTaskWorker(
            [first_task, second_task],
            FakeAdb(),
            Path("screenshots"),
            0,
        )
        second_engine = MagicMock()
        second_engine.run.return_value = RunResult(second_task.id, RunStatus.SUCCESS, 1, 1)
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with patch("free_app.worker.prepare_device", return_value=True), patch(
            "free_app.worker.shutdown_mumu", return_value=True
        ), patch("free_app.worker.cleanup_apps"), patch(
            "free_app.worker.send_run_notification"
        ), patch.object(
            worker,
            "_make_engine",
            side_effect=[RuntimeError("engine unavailable"), second_engine],
        ) as make_engine:
            worker.run()

        self.assertEqual(make_engine.call_count, 2)
        second_engine.run.assert_called_once_with(second_task)
        summary = finished[0]
        self.assertEqual(summary.status, RunStatus.FAILED)
        self.assertEqual(summary.completed_tasks, 2)
        self.assertEqual(summary.failed_task, first_task.id)
        self.assertEqual([result.task_id for result in summary.results], ["first", "second"])

    def test_single_worker_prepare_failure_shuts_down_and_notifies(self) -> None:
        task = make_task()
        worker = TaskWorker(
            task,
            FakeAdb(),
            Path("screenshots"),
            0,
            settings={"close_mumu_after_run": True},
        )
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with patch(
            "free_app.worker.prepare_device",
            side_effect=AdbError("设备超时"),
        ), patch("free_app.worker.shutdown_mumu", return_value=True) as shutdown, patch(
            "free_app.worker.send_run_notification"
        ) as notify, patch("free_app.worker._prune_outputs") as prune:
            worker.run()

        shutdown.assert_called_once()
        notify.assert_called_once()
        prune.assert_called_once()
        self.assertEqual(finished[0].status, RunStatus.FAILED)

    def test_single_worker_keeps_result_when_final_cleanup_fails(self) -> None:
        task = make_task()
        worker = TaskWorker(task, FakeAdb(), Path("screenshots"), 0)
        worker.engine.run = MagicMock(return_value=RunResult(task.id, RunStatus.SUCCESS, 1, 1))
        finished: list[object] = []
        worker.finished.connect(finished.append)
        logs: list[str] = []
        worker.log_message.connect(logs.append)
        with patch("free_app.worker.prepare_device", return_value=True), patch(
            "free_app.worker.cleanup_apps", side_effect=RuntimeError("cleanup failed")
        ), patch("free_app.worker.shutdown_mumu", return_value=True), patch(
            "free_app.worker.send_run_notification"
        ), patch("free_app.worker._prune_outputs"):
            worker.run()

        self.assertEqual(finished[0].status, RunStatus.SUCCESS)
        self.assertTrue(any("cleanup failed" in message for message in logs))

    def test_single_worker_stop_during_prepare_returns_stopped(self) -> None:
        worker = TaskWorker(
            make_task(),
            FakeAdb(),
            Path("screenshots"),
            0,
            settings={"close_mumu_after_run": True},
        )
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with patch(
            "free_app.worker.prepare_device",
            side_effect=MuMuStopRequested("用户停止"),
        ), patch("free_app.worker.shutdown_mumu", return_value=True), patch(
            "free_app.worker.send_run_notification"
        ):
            worker.run()

        self.assertEqual(finished[0].status, RunStatus.STOPPED)

    def test_single_worker_stop_before_run_is_not_cleared(self) -> None:
        adb = FakeAdb()
        worker = TaskWorker(
            make_task(),
            adb,
            Path("screenshots"),
            0,
            settings={"close_mumu_after_run": True},
        )
        finished: list[object] = []
        worker.finished.connect(finished.append)
        worker.stop()

        with patch(
            "free_app.worker.prepare_device",
            return_value=adb.select_device(),
        ), patch("free_app.worker.shutdown_mumu", return_value=True), patch(
            "free_app.worker.send_run_notification"
        ):
            worker.run()

        self.assertEqual(finished[0].status, RunStatus.STOPPED)
        self.assertEqual(adb.taps, [])

    def test_batch_worker_stop_during_prepare_emits_stopped_result(self) -> None:
        task = make_task()
        worker = BatchTaskWorker([task], FakeAdb(), Path("screenshots"), 0)
        finished: list[object] = []
        task_finished: list[object] = []
        worker.finished.connect(finished.append)
        worker.task_finished.connect(task_finished.append)
        with patch(
            "free_app.worker.prepare_device",
            side_effect=MuMuStopRequested("user stopped"),
        ), patch("free_app.worker.shutdown_mumu", return_value=True), patch(
            "free_app.worker.send_run_notification"
        ):
            worker.run()

        summary = finished[0]
        self.assertEqual(summary.status, RunStatus.STOPPED)
        self.assertEqual(summary.completed_tasks, 1)
        self.assertEqual(len(task_finished), 1)
        self.assertEqual(task_finished[0].status, RunStatus.STOPPED)

    def test_batch_worker_prepare_failure_shuts_down_and_notifies(self) -> None:
        task = make_task()
        worker = BatchTaskWorker(
            [task],
            FakeAdb(),
            Path("screenshots"),
            0,
            settings={"close_mumu_after_run": True},
        )
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with patch(
            "free_app.worker.prepare_device",
            side_effect=AdbError("批量设备超时"),
        ), patch("free_app.worker.shutdown_mumu", return_value=True) as shutdown, patch(
            "free_app.worker.send_run_notification"
        ) as notify:
            worker.run()

        shutdown.assert_called_once()
        notify.assert_called_once()
        summary = finished[0]
        self.assertEqual(summary.status, RunStatus.FAILED)
        self.assertEqual(len(summary.results), 1)
        self.assertEqual(summary.results[0].failed_step, "准备连接设备")

    def test_batch_worker_marks_summary_failed_when_config_errors_exist(self) -> None:
        task = make_task()
        config_error = TaskFileError(Path("broken.json"), "invalid action")
        worker = BatchTaskWorker(
            [task],
            FakeAdb(),
            Path("screenshots"),
            0,
            config_errors=(config_error,),
        )
        engine = MagicMock()
        engine.run.return_value = RunResult(task.id, RunStatus.SUCCESS, 1, 1)
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with patch("free_app.worker.prepare_device", return_value=True), patch(
            "free_app.worker.shutdown_mumu", return_value=True
        ), patch("free_app.worker.cleanup_apps"), patch(
            "free_app.worker.send_run_notification"
        ) as notify, patch.object(worker, "_make_engine", return_value=engine):
            worker.run()

        summary = finished[0]
        self.assertEqual(summary.status, RunStatus.FAILED)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[4][0], config_error)

    def test_batch_worker_runs_all_shipped_tasks_and_notifies(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        tasks, errors = load_task_directory(
            project_root / "config" / "tasks",
            {"qq_group_name": "测试群"},
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(tasks), 5)

        worker = BatchTaskWorker(
            tasks,
            FakeAdb(),
            Path("screenshots"),
            0,
            settings={"task_execution_counts": {}},
        )
        engines: list[tuple[str, MagicMock]] = []

        def make_engine(task_id: str) -> MagicMock:
            engine = MagicMock()
            engine.run.return_value = RunResult(task_id, RunStatus.SUCCESS, 1, 1)
            engines.append((task_id, engine))
            return engine

        finished: list[object] = []
        worker.finished.connect(finished.append)
        with patch("free_app.worker.prepare_device", return_value=True), patch(
            "free_app.worker.shutdown_mumu", return_value=True
        ), patch("free_app.worker.shutdown_mumu_app", return_value=True), patch(
            "free_app.worker.cleanup_apps"
        ), patch(
            "free_app.worker.send_run_notification"
        ) as notify, patch.object(worker, "_make_engine", side_effect=make_engine):
            worker.run()

        self.assertEqual(
            [task_id for task_id, _engine in engines],
            [task.id for task in tasks],
        )
        summary = finished[0]
        self.assertEqual(summary.status, RunStatus.SUCCESS)
        self.assertEqual(summary.total_tasks, 5)
        self.assertEqual(summary.completed_tasks, 5)
        notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
