from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal, Slot

from .adb import AdbClient
from .app_lifecycle import cleanup_apps
from .config import TaskFileError, resolve_path
from .engine import AutomationEngine
from .helpers import LogCallback
from .models import BatchRunResult, RunResult, RunStatus, TaskDefinition
from .mumu import (
    MuMuStopRequested,
    connect_to_running_mumu,
    prepare_device,
    shutdown_mumu,
    shutdown_mumu_app,
)
from .notifications import send_run_notification
from .onnx_ocr import build_ocr_client
from .pruning import prune_files
from .task_runner import run_task_executions, task_execution_count


def reconnect_device(
    adb: AdbClient,
    log_callback: Callable[[str], None],
) -> bool:
    """Reconnect the selected network ADB device."""

    try:
        if adb.reconnect():
            log_callback("ADB 设备已重新连接")
            return True
    except Exception as exc:
        log_callback(f"ADB 设备重连异常: {exc}")
    return False


def _prune_outputs(
    settings: dict[str, Any],
    base_directory: Path,
    log_callback: Callable[[str], None],
) -> None:
    try:
        log_max = int(settings.get("max_log_files", 0))
        screenshot_max = int(settings.get("max_screenshot_files", 0))
    except (TypeError, ValueError):
        return
    mode = str(settings.get("cleanup_mode", "recycle"))
    prune_files(
        resolve_path(settings.get("log_directory"), base_directory),
        log_max,
        mode,
        log_callback,
    )
    prune_files(
        resolve_path(settings.get("screenshot_directory"), base_directory),
        screenshot_max,
        mode,
        log_callback,
    )


def _shutdown_all(settings: dict[str, Any], log: LogCallback) -> None:
    """Shut down the MuMu instance and its companion app together."""

    shutdown_mumu(settings, log)
    shutdown_mumu_app(settings, log)


class TaskWorker(QObject):
    log_message = Signal(str)
    progress = Signal(int, int, str)
    finished = Signal(object)

    def __init__(
        self,
        task: TaskDefinition,
        adb: AdbClient,
        screenshot_directory: Path,
        poll_interval: float,
        screenshots_enabled: bool = True,
        settings: dict[str, Any] | None = None,
        base_directory: Path | None = None,
        config_errors: tuple[TaskFileError, ...] = (),
        debug: bool = False,
    ):
        super().__init__()
        self.task = task
        self.settings = settings or {}
        self.config_errors = tuple(config_errors)
        self.screenshots_enabled = screenshots_enabled
        self.debug = bool(debug)
        self._stop_requested = Event()
        self.base_directory = base_directory or Path.cwd()
        ocr_client = build_ocr_client(
            self.settings,
            self.base_directory,
            log_callback=self.log_message.emit,
        )
        self.engine = AutomationEngine(
            adb,
            screenshot_directory=screenshot_directory,
            log_callback=self.log_message.emit,
            progress_callback=self.progress.emit,
            poll_interval=poll_interval,
            ocr_client=ocr_client,
            ocr_boxes_client=ocr_client.recognize_with_boxes,
            screenshots_enabled=self.screenshots_enabled,
        )

    @Slot()
    def run(self) -> None:
        prepared = False
        result: RunResult | None = None
        try:
            if self.debug:
                self.log_message.emit("调试模式：直接连接已运行的 MuMu 实例")
                connect_to_running_mumu(
                    self.engine.adb,
                    self.settings,
                    self.log_message.emit,
                )
            else:
                prepare_device(
                    self.engine.adb,
                    self.settings,
                    self.log_message.emit,
                    stop_event=self._stop_requested,
                )
            prepared = True
            for config_error in self.config_errors:
                self.log_message.emit(
                    f"{config_error.path.name}已损坏，跳过该任务：{config_error.reason}"
                )
            self.log_message.emit(f"开始任务: {self.task.name} ({self.task.id})")
            result = self._run_attempt()
            error = f", error={result.error}" if result.error else ""
            self.log_message.emit(
                f"任务结束: status={result.status.value}, "
                f"completed={result.completed_steps}/{result.total_steps}{error}"
            )
        except MuMuStopRequested:
            self.log_message.emit("设备准备已停止，不再启动任务")
            result = RunResult.stopped(self.task.id, len(self.task.actions))
        except Exception as exc:
            self.log_message.emit(f"设备准备失败: {exc}")
            result = RunResult.failed(
                self.task.id,
                len(self.task.actions),
                error=str(exc),
            )
        finally:
            if self.debug:
                self.log_message.emit("调试模式：跳过 App 清理与 MuMu 关闭")
            elif prepared:
                try:
                    cleanup_apps(self.engine.adb, self.settings, self.log_message.emit)
                except Exception as exc:
                    self.log_message.emit(f"App 清理失败: {exc}")
                finally:
                    _shutdown_all(self.settings, self.log_message.emit)
            else:
                # MuMu may already have been started when a later connection
                # timeout or device validation failed, so shutdown still runs.
                _shutdown_all(self.settings, self.log_message.emit)
        if result is None:
            result = RunResult.failed(
                self.task.id,
                len(self.task.actions),
                error="设备准备失败",
            )
        if not self.debug:
            send_run_notification(
                self.settings, result, [self.task], self.log_message.emit, self.config_errors
            )
        _prune_outputs(self.settings, self.base_directory, self.log_message.emit)
        self.finished.emit(result)

    def _run_attempt(self) -> RunResult:
        try:
            return self.engine.run(self.task)
        except Exception as exc:
            self.log_message.emit(f"任务异常: {exc}")
            return RunResult.failed(
                self.task.id,
                len(self.task.actions),
                failed_step="执行任务",
                error=str(exc),
            )

    @Slot()
    def stop(self) -> None:
        self._stop_requested.set()
        self.engine.request_stop()


class BatchTaskWorker(QObject):
    """Run configured tasks sequentially and isolate each task with cleanup."""

    log_message = Signal(str)
    task_started = Signal(str, int, int)
    progress = Signal(str, int, int, str)
    task_finished = Signal(object)
    finished = Signal(object)

    def __init__(
        self,
        tasks: list[TaskDefinition] | tuple[TaskDefinition, ...],
        adb: AdbClient,
        screenshot_directory: Path,
        poll_interval: float,
        screenshots_enabled: bool = True,
        settings: dict[str, Any] | None = None,
        base_directory: Path | None = None,
        config_errors: tuple[TaskFileError, ...] = (),
    ):
        super().__init__()
        self.tasks = tuple(tasks)
        self.adb = adb
        self.settings = settings or {}
        self.config_errors = tuple(config_errors)
        self.screenshot_directory = screenshot_directory
        self.poll_interval = poll_interval
        self.screenshots_enabled = screenshots_enabled
        self.base_directory = base_directory or Path.cwd()
        self._stop_requested = Event()
        self._current_engine: AutomationEngine | None = None

    def _make_engine(self, task_id: str) -> AutomationEngine:
        ocr_client = build_ocr_client(
            self.settings,
            self.base_directory,
            log_callback=self.log_message.emit,
        )

        def report_progress(index: int, total: int, description: str) -> None:
            self.progress.emit(task_id, index, total, description)

        return AutomationEngine(
            self.adb,
            screenshot_directory=self.screenshot_directory,
            log_callback=self.log_message.emit,
            progress_callback=report_progress,
            poll_interval=self.poll_interval,
            ocr_client=ocr_client,
            ocr_boxes_client=ocr_client.recognize_with_boxes,
            screenshots_enabled=self.screenshots_enabled,
        )

    @Slot()
    def run(self) -> None:
        results: list[RunResult] = []
        setup_error: str | None = None
        stop_during_setup = False
        try:
            prepare_device(
                self.adb,
                self.settings,
                self.log_message.emit,
                stop_event=self._stop_requested,
            )
            for config_error in self.config_errors:
                self.log_message.emit(
                    f"{config_error.path.name}已损坏，跳过该任务：{config_error.reason}"
                )
            for index, task in enumerate(self.tasks, start=1):
                if self._stop_requested.is_set():
                    break
                execution_count = task_execution_count(self.settings, task_id=task.id)
                if execution_count <= 0:
                    continue
                self.task_started.emit(task.id, index, len(self.tasks))
                self.log_message.emit(f"开始任务 [{index}/{len(self.tasks)}]: {task.name}")
                result = run_task_executions(
                    task,
                    lambda: self._run_attempt(task),
                    execution_count,
                    self.log_message.emit,
                    cleanup_callback=lambda: cleanup_apps(
                        self.adb,
                        self.settings,
                        self.log_message.emit,
                    ),
                    reconnect_callback=lambda: reconnect_device(
                        self.adb,
                        self.log_message.emit,
                    ),
                    stop_event=self._stop_requested,
                )
                try:
                    cleanup_apps(self.adb, self.settings, self.log_message.emit)
                except Exception as exc:
                    self.log_message.emit(f"App 清理失败: {exc}")

                results.append(result)
                self.task_finished.emit(result)
                if result.status == RunStatus.STOPPED or self._stop_requested.is_set():
                    break
        except MuMuStopRequested:
            stop_during_setup = True
            self.log_message.emit("设备准备已停止，不再启动任务")
        except Exception as exc:
            setup_error = str(exc)
            self.log_message.emit(f"批量任务准备失败: {exc}")
            if self.tasks and not results:
                self._emit_prepare_failure(
                    self.tasks[0], RunStatus.FAILED, setup_error, results
                )
        finally:
            _shutdown_all(self.settings, self.log_message.emit)

        if not results and self.tasks and (stop_during_setup or setup_error):
            task = self.tasks[0]
            status = RunStatus.STOPPED if stop_during_setup else RunStatus.FAILED
            self._emit_prepare_failure(task, status, setup_error, results)

        stopped = self._stop_requested.is_set() or stop_during_setup or any(
            result.status == RunStatus.STOPPED for result in results
        )
        failed_task = next(
            (result.task_id for result in results if result.status == RunStatus.FAILED),
            None,
        )
        status = (
            RunStatus.STOPPED
            if stopped
            else RunStatus.FAILED
            if failed_task or self.config_errors
            else RunStatus.SUCCESS
        )
        summary = BatchRunResult(
            status=status,
            results=tuple(results),
            total_tasks=len(self.tasks),
            completed_tasks=len(results),
            failed_task=failed_task,
            error=setup_error,
        )
        send_run_notification(
            self.settings, summary, self.tasks, self.log_message.emit, self.config_errors
        )
        _prune_outputs(self.settings, self.base_directory, self.log_message.emit)
        self.finished.emit(summary)

    def _emit_prepare_failure(
        self,
        task: TaskDefinition,
        status: RunStatus,
        error: str | None,
        results: list[RunResult],
    ) -> None:
        """Fabricate and emit a prepare failure/stopped result for ``task``.

        The result is appended to ``results`` and its ``task_started`` is
        emitted before ``task_finished``, preserving the callback order of the
        original inline blocks.
        """

        if status == RunStatus.STOPPED:
            result = RunResult.stopped(task.id, len(task.actions))
        else:
            result = RunResult.failed(task.id, len(task.actions), error=error)
        results.append(result)
        self.task_started.emit(task.id, 1, len(self.tasks))
        self.task_finished.emit(result)

    def _run_attempt(self, task: TaskDefinition) -> RunResult:
        engine: AutomationEngine | None = None
        try:
            engine = self._make_engine(task.id)
            self._current_engine = engine
            return engine.run(task)
        except Exception as exc:
            self.log_message.emit(f"任务异常: {exc}")
            return RunResult.failed(
                task.id,
                len(task.actions),
                failed_step="执行任务",
                error=str(exc),
            )
        finally:
            self._current_engine = None

    @Slot()
    def stop(self) -> None:
        self._stop_requested.set()
        if self._current_engine is not None:
            self._current_engine.request_stop()
