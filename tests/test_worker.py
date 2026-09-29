from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from free_app.adb import AdbError
from free_app.config import TaskFileError, load_settings, load_task_directory
from free_app.models import Action, RunResult, RunStatus, TaskDefinition
from free_app.mumu import MuMuStopRequested
from free_app.worker import (
    BatchTaskWorker,
    TaskWorker,
    _fatal_device_error,
    _prune_outputs,
    reconnect_device,
)


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
        actions=(Action("click", {"texts": ["领取"], "timeout_seconds": 0}),),
    )


def sanitized_settings(**overrides: Any) -> dict[str, Any]:
    """构建 ``load_settings`` 保证的完整清洗后的设置字典。

    worker 现在信任清洗层的保证（每个键都存在、每个值类型正确），
    因此测试必须传入真实的清洗后字典，而不是手工拼出的残缺字典。
    """

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "settings.json"
        path.write_text(json.dumps(overrides, ensure_ascii=False), encoding="utf-8")
        return load_settings(path)


class WorkerTests(unittest.TestCase):
    def test_fatal_device_error_matches_offline_and_timeout_messages(self) -> None:
        """事故日志中的真实错误串必须被判为不可重试。"""

        fatal_messages = [
            "设备 127.0.0.1:16416 当前状态为 offline",
            "ADB 命令失败: adb.exe: device offline",
            "MuMu ADB 设备未上线: 127.0.0.1:16416",
            "MuMu 实例 1 未返回动态 ADB 地址",
            "等待 MuMu 动态 ADB 地址超时，最后状态: 未分配",
            "等待 MuMu ADB 设备超时: 127.0.0.1:16416",
            "error: device not found",
        ]
        for message in fatal_messages:
            with self.subTest(message=message):
                self.assertTrue(_fatal_device_error(message))

    def test_fatal_device_error_ignores_action_level_failures(self) -> None:
        """动作级失败应当照常重试，不能被误判为设备级故障。"""

        retryable = [
            "UI 未找到可点击目标: {'texts': ['签到']}",
            "OCR 未找到可点击目标",
            "目标控件已禁用: 签到",
            "目标控件没有可点击区域: 签到",
            "compound 展开失败",
        ]
        for message in retryable:
            with self.subTest(message=message):
                self.assertFalse(_fatal_device_error(message))

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
            settings = sanitized_settings(
                log_directory="logs",
                screenshot_directory="screenshots",
                max_log_files=-1,
                max_screenshot_files=1,
                cleanup_mode="permanent",
            )

            _prune_outputs(settings, base, lambda _message: None)

            remaining = sorted(path.name for path in screenshots.glob("*.png"))
            self.assertEqual(remaining, ["2.png"])
            self.assertEqual(len(list(logs.glob("*.log"))), 1)

    def test_prune_outputs_rejects_dirty_max_files_types(self) -> None:
        # int() try/except 已删除：绕过清洗层的脏类型直接抛错，不再静默回退。
        with self.assertRaises(ValueError):
            _prune_outputs({"max_log_files": "many"}, Path("."), lambda _message: None)
        with self.assertRaises(TypeError):
            _prune_outputs(
                {"max_screenshot_files": None}, Path("."), lambda _message: None
            )

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
            settings=sanitized_settings(task_execution_counts={task.id: 3}),
        )
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with (
            patch(
                "free_app.worker.prepare_device",
                return_value=True,
            ),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.cleanup_apps"),
            patch("free_app.worker.send_run_notification") as notify,
            patch.object(
                worker.engine,
                "run",
                return_value=RunResult(task.id, RunStatus.SUCCESS, 1, 1),
            ) as run,
        ):
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
            settings=sanitized_settings(),
        )
        with patch.object(
            worker.engine, "run", side_effect=RuntimeError("engine crashed")
        ):
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
            settings=sanitized_settings(),
            debug=True,
        )
        with (
            patch("free_app.worker.prepare_device") as prepare,
            patch(
                "free_app.worker.connect_to_running_mumu",
                return_value=True,
            ) as connect,
            patch("free_app.worker.cleanup_apps") as cleanup,
            patch("free_app.worker.shutdown_mumu") as shutdown,
            patch("free_app.worker.shutdown_mumu_app") as shutdown_app,
            patch("free_app.worker.send_run_notification") as notify,
            patch.object(
                worker.engine,
                "run",
                return_value=RunResult(task.id, RunStatus.SUCCESS, 1, 1),
            ),
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
            settings=sanitized_settings(task_execution_counts={task.id: 0}),
        )
        with (
            patch("free_app.worker.prepare_device", return_value=True),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.cleanup_apps"),
            patch("free_app.worker.send_run_notification"),
            patch.object(
                worker.engine,
                "run",
                return_value=RunResult(task.id, RunStatus.FAILED, 0, 1, error="失败"),
            ) as run,
        ):
            worker.run()

        run.assert_called_once()

    def test_batch_worker_retries_failed_task_up_to_execution_count(self) -> None:
        task = make_task()
        worker = BatchTaskWorker(
            [task],
            FakeAdb(),
            Path("screenshots"),
            0,
            settings=sanitized_settings(task_execution_counts={task.id: 2}),
        )
        first_engine = MagicMock()
        first_engine.run.return_value = RunResult(
            task.id, RunStatus.FAILED, 0, 1, error="第一次失败"
        )
        second_engine = MagicMock()
        second_engine.run.return_value = RunResult(task.id, RunStatus.SUCCESS, 1, 1)
        finished: list[object] = []
        task_finished: list[object] = []
        worker.finished.connect(finished.append)
        worker.task_finished.connect(task_finished.append)
        with (
            patch("free_app.worker.prepare_device", return_value=True),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.cleanup_apps"),
            patch("free_app.worker.send_run_notification"),
            patch.object(
                worker, "_make_engine", side_effect=[first_engine, second_engine]
            ),
        ):
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
            settings=sanitized_settings(task_execution_counts={task.id: 2}),
        )
        first_engine = MagicMock()
        first_engine.run.return_value = RunResult(
            task.id, RunStatus.FAILED, 0, 1, error="第一次失败"
        )
        second_engine = MagicMock()
        second_engine.run.return_value = RunResult(task.id, RunStatus.SUCCESS, 1, 1)
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with (
            patch("free_app.worker.prepare_device", return_value=True),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.cleanup_apps"),
            patch("free_app.worker.send_run_notification"),
            patch.object(
                worker, "_make_engine", side_effect=[first_engine, second_engine]
            ),
        ):
            worker.run()

        self.assertEqual(adb.reconnect_count, 1)
        self.assertEqual(finished[0].status, RunStatus.SUCCESS)

    def test_batch_worker_skips_mumu_shutdown_when_lock_not_acquired(self) -> None:
        """拿不到实例锁说明实例正被别人使用，收尾绝不能关闭它。"""

        from free_app.lock import LockError

        task = make_task()
        worker = BatchTaskWorker(
            [task],
            FakeAdb(),
            Path("screenshots"),
            0,
            settings=sanitized_settings(),
        )
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with (
            patch.object(
                worker,
                "_acquire_instance_lock",
                side_effect=LockError("实例被另一个 FREE 进程占用"),
            ),
            patch("free_app.worker.shutdown_mumu") as shutdown_mumu,
            patch("free_app.worker.shutdown_mumu_app") as shutdown_app,
            patch("free_app.worker.cleanup_apps"),
            patch("free_app.worker.send_run_notification"),
        ):
            worker.run()

        shutdown_mumu.assert_not_called()
        shutdown_app.assert_not_called()
        self.assertEqual(finished[0].status, RunStatus.FAILED)

    def test_task_worker_skips_mumu_shutdown_when_lock_not_acquired(self) -> None:
        from free_app.lock import LockError

        task = make_task()
        worker = TaskWorker(
            task,
            FakeAdb(),
            Path("screenshots"),
            True,
            settings=sanitized_settings(),
        )
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with (
            patch.object(
                worker,
                "_acquire_instance_lock",
                side_effect=LockError("实例被另一个 FREE 进程占用"),
            ),
            patch("free_app.worker.shutdown_mumu") as shutdown_mumu,
            patch("free_app.worker.shutdown_mumu_app") as shutdown_app,
            patch("free_app.worker.cleanup_apps"),
            patch("free_app.worker.send_run_notification"),
        ):
            worker.run()

        shutdown_mumu.assert_not_called()
        shutdown_app.assert_not_called()
        self.assertEqual(finished[0].status, RunStatus.FAILED)

    def test_worker_releases_instance_lock_after_run(self) -> None:
        task = make_task()
        worker = BatchTaskWorker(
            [task],
            FakeAdb(),
            Path("screenshots"),
            0,
            settings=sanitized_settings(),
        )
        engine = MagicMock()
        engine.run.return_value = RunResult(task.id, RunStatus.SUCCESS, 1, 1)
        with (
            patch("free_app.worker.prepare_device", return_value=True),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.shutdown_mumu_app", return_value=True),
            patch("free_app.worker.cleanup_apps"),
            patch("free_app.worker.send_run_notification"),
            patch.object(worker, "_make_engine", return_value=engine),
        ):
            worker.run()

        self.assertFalse(worker._lock_acquired)
        self.assertIsNone(worker._instance_lock)

    def test_batch_worker_stops_after_first_success_when_count_is_higher(self) -> None:
        task = make_task()
        worker = BatchTaskWorker(
            [task],
            FakeAdb(),
            Path("screenshots"),
            0,
            settings=sanitized_settings(task_execution_counts={task.id: 3}),
        )
        engine = MagicMock()
        engine.run.return_value = RunResult(task.id, RunStatus.SUCCESS, 1, 1)
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with (
            patch("free_app.worker.prepare_device", return_value=True),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.cleanup_apps"),
            patch("free_app.worker.send_run_notification"),
            patch.object(worker, "_make_engine", return_value=engine),
        ):
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
            settings=sanitized_settings(),
        )
        engine = MagicMock()

        def stop_during_run(_task: TaskDefinition) -> RunResult:
            worker.stop()
            return RunResult(first_task.id, RunStatus.STOPPED, 0, 1)

        engine.run.side_effect = stop_during_run
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with (
            patch("free_app.worker.prepare_device", return_value=True),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.cleanup_apps"),
            patch("free_app.worker.send_run_notification"),
            patch.object(worker, "_make_engine", return_value=engine) as make_engine,
        ):
            worker.run()

        engine.request_stop.assert_called_once()
        make_engine.assert_called_once_with(first_task.id)
        summary = finished[0]
        self.assertEqual(summary.status, RunStatus.STOPPED)
        self.assertEqual(summary.completed_tasks, 1)
        self.assertEqual(
            [result.task_id for result in summary.results], [first_task.id]
        )

    def test_batch_engine_creation_failure_does_not_skip_later_tasks(self) -> None:
        first_task = make_task("first")
        second_task = make_task("second")
        worker = BatchTaskWorker(
            [first_task, second_task],
            FakeAdb(),
            Path("screenshots"),
            0,
            settings=sanitized_settings(),
        )
        second_engine = MagicMock()
        second_engine.run.return_value = RunResult(
            second_task.id, RunStatus.SUCCESS, 1, 1
        )
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with (
            patch("free_app.worker.prepare_device", return_value=True),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.cleanup_apps"),
            patch("free_app.worker.send_run_notification"),
            patch.object(
                worker,
                "_make_engine",
                side_effect=[RuntimeError("engine unavailable"), second_engine],
            ) as make_engine,
        ):
            worker.run()

        self.assertEqual(make_engine.call_count, 2)
        second_engine.run.assert_called_once_with(second_task)
        summary = finished[0]
        self.assertEqual(summary.status, RunStatus.FAILED)
        self.assertEqual(summary.completed_tasks, 2)
        self.assertEqual(summary.failed_task, first_task.id)
        self.assertEqual(
            [result.task_id for result in summary.results], ["first", "second"]
        )

    def test_batch_worker_continues_after_failed_task(self) -> None:
        first_task = make_task("first")
        second_task = make_task("second")
        worker = BatchTaskWorker(
            [first_task, second_task],
            FakeAdb(),
            Path("screenshots"),
            0,
            settings=sanitized_settings(),
        )
        first_engine = MagicMock()
        first_engine.run.return_value = RunResult(
            first_task.id, RunStatus.FAILED, 0, 1, error="第一个任务失败"
        )
        second_engine = MagicMock()
        second_engine.run.return_value = RunResult(
            second_task.id, RunStatus.SUCCESS, 1, 1
        )
        finished: list[object] = []
        task_finished: list[object] = []
        worker.finished.connect(finished.append)
        worker.task_finished.connect(task_finished.append)
        with (
            patch("free_app.worker.prepare_device", return_value=True),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.cleanup_apps"),
            patch("free_app.worker.send_run_notification"),
            patch.object(
                worker,
                "_make_engine",
                side_effect=[first_engine, second_engine],
            ) as make_engine,
        ):
            worker.run()

        self.assertEqual(make_engine.call_count, 2)
        second_engine.run.assert_called_once_with(second_task)
        summary = finished[0]
        self.assertEqual(summary.status, RunStatus.FAILED)
        self.assertEqual(summary.completed_tasks, 2)
        self.assertEqual(summary.failed_task, first_task.id)
        self.assertEqual(
            [result.task_id for result in summary.results], ["first", "second"]
        )
        self.assertEqual(
            [result.status for result in summary.results],
            [RunStatus.FAILED, RunStatus.SUCCESS],
        )

    def test_batch_worker_continues_after_task_execution_exception(self) -> None:
        first_task = make_task("first")
        second_task = make_task("second")
        worker = BatchTaskWorker(
            [first_task, second_task],
            FakeAdb(),
            Path("screenshots"),
            0,
            settings=sanitized_settings(),
        )
        second_engine = MagicMock()
        second_engine.run.return_value = RunResult(
            second_task.id, RunStatus.SUCCESS, 1, 1
        )
        real_run_attempt = worker._run_attempt

        def flaky_run_attempt(task: TaskDefinition) -> RunResult:
            if task.id == first_task.id:
                raise RuntimeError("task runner crashed")
            return real_run_attempt(task)

        finished: list[object] = []
        worker.finished.connect(finished.append)
        with (
            patch("free_app.worker.prepare_device", return_value=True),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.cleanup_apps"),
            patch("free_app.worker.send_run_notification"),
            patch.object(
                worker,
                "_make_engine",
                return_value=second_engine,
            ),
            patch.object(
                worker,
                "_run_attempt",
                side_effect=flaky_run_attempt,
            ),
        ):
            worker.run()

        summary = finished[0]
        self.assertEqual(summary.status, RunStatus.FAILED)
        self.assertEqual(summary.completed_tasks, 2)
        second_engine.run.assert_called_once_with(second_task)
        self.assertEqual(
            [result.task_id for result in summary.results], ["first", "second"]
        )
        self.assertEqual(
            [result.status for result in summary.results],
            [RunStatus.FAILED, RunStatus.SUCCESS],
        )

    def test_single_worker_prepare_failure_shuts_down_and_notifies(self) -> None:
        task = make_task()
        worker = TaskWorker(
            task,
            FakeAdb(),
            Path("screenshots"),
            0,
            settings=sanitized_settings(close_mumu_after_run=True),
        )
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with (
            patch(
                "free_app.worker.prepare_device",
                side_effect=AdbError("设备超时"),
            ),
            patch("free_app.worker.shutdown_mumu", return_value=True) as shutdown,
            patch("free_app.worker.send_run_notification") as notify,
            patch("free_app.worker._prune_outputs") as prune,
        ):
            worker.run()

        shutdown.assert_called_once()
        notify.assert_called_once()
        prune.assert_called_once()
        self.assertEqual(finished[0].status, RunStatus.FAILED)

    def test_single_worker_keeps_result_when_final_cleanup_fails(self) -> None:
        task = make_task()
        worker = TaskWorker(
            task, FakeAdb(), Path("screenshots"), 0, settings=sanitized_settings()
        )
        worker.engine.run = MagicMock(
            return_value=RunResult(task.id, RunStatus.SUCCESS, 1, 1)
        )
        finished: list[object] = []
        worker.finished.connect(finished.append)
        logs: list[str] = []
        worker.log_message.connect(logs.append)
        with (
            patch("free_app.worker.prepare_device", return_value=True),
            patch(
                "free_app.worker.cleanup_apps",
                side_effect=RuntimeError("cleanup failed"),
            ),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.send_run_notification"),
            patch("free_app.worker._prune_outputs"),
        ):
            worker.run()

        self.assertEqual(finished[0].status, RunStatus.SUCCESS)
        self.assertTrue(any("cleanup failed" in message for message in logs))

    def test_single_worker_stop_during_prepare_returns_stopped(self) -> None:
        worker = TaskWorker(
            make_task(),
            FakeAdb(),
            Path("screenshots"),
            0,
            settings=sanitized_settings(close_mumu_after_run=True),
        )
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with (
            patch(
                "free_app.worker.prepare_device",
                side_effect=MuMuStopRequested("用户停止"),
            ),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.send_run_notification"),
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
            settings=sanitized_settings(
                close_mumu_after_run=True, cleanup_delay_seconds=0
            ),
        )
        finished: list[object] = []
        worker.finished.connect(finished.append)
        worker.stop()

        with (
            patch(
                "free_app.worker.prepare_device",
                return_value=adb.select_device(),
            ),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.send_run_notification"),
        ):
            worker.run()

        self.assertEqual(finished[0].status, RunStatus.STOPPED)
        self.assertEqual(adb.taps, [])

    def test_batch_worker_stop_during_prepare_emits_stopped_result(self) -> None:
        task = make_task()
        worker = BatchTaskWorker(
            [task], FakeAdb(), Path("screenshots"), 0, settings=sanitized_settings()
        )
        finished: list[object] = []
        task_finished: list[object] = []
        worker.finished.connect(finished.append)
        worker.task_finished.connect(task_finished.append)
        with (
            patch(
                "free_app.worker.prepare_device",
                side_effect=MuMuStopRequested("user stopped"),
            ),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.send_run_notification"),
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
            settings=sanitized_settings(close_mumu_after_run=True),
        )
        finished: list[object] = []
        worker.finished.connect(finished.append)
        with (
            patch(
                "free_app.worker.prepare_device",
                side_effect=AdbError("批量设备超时"),
            ),
            patch("free_app.worker.shutdown_mumu", return_value=True) as shutdown,
            patch("free_app.worker.send_run_notification") as notify,
        ):
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
        with (
            patch("free_app.worker.prepare_device", return_value=True),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.cleanup_apps"),
            patch("free_app.worker.send_run_notification") as notify,
            patch.object(worker, "_make_engine", return_value=engine),
        ):
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
            settings=sanitized_settings(task_execution_counts={}),
        )
        engines: list[tuple[str, MagicMock]] = []

        def make_engine(task_id: str) -> MagicMock:
            engine = MagicMock()
            engine.run.return_value = RunResult(task_id, RunStatus.SUCCESS, 1, 1)
            engines.append((task_id, engine))
            return engine

        finished: list[object] = []
        worker.finished.connect(finished.append)
        with (
            patch("free_app.worker.prepare_device", return_value=True),
            patch("free_app.worker.shutdown_mumu", return_value=True),
            patch("free_app.worker.shutdown_mumu_app", return_value=True),
            patch("free_app.worker.cleanup_apps"),
            patch("free_app.worker.send_run_notification") as notify,
            patch.object(worker, "_make_engine", side_effect=make_engine),
        ):
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
