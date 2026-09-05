from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from .adb import AdbClient
from .app_lifecycle import cleanup_apps
from .config import TaskFileError, resolve_path
from .engine import AutomationEngine
from .helpers import LogCallback, ProgressCallback
from .models import BatchRunResult, RunResult, RunStatus, TaskDefinition
from .mumu import (
    MuMuStopRequested,
    connect_to_running_mumu,
    prepare_device,
    shutdown_mumu,
    shutdown_mumu_app,
)
from .notifications import send_run_notification
from .onnx_ocr import OnnxOcrClient, build_ocr_client
from .pruning import prune_files
from .task_runner import run_task_executions, task_execution_count


def _build_engine(
    adb: AdbClient,
    screenshot_directory: Path,
    settings: dict[str, Any],
    base_directory: Path,
    log_callback: LogCallback,
    progress_callback: ProgressCallback,
    screenshots_enabled: bool,
    ocr_client: OnnxOcrClient,
) -> AutomationEngine:
    """围绕已构建好的 OCR client 组装引擎。

    OCR client 由调用方传入，使昂贵的 ONNX 会话初始化在单任务
    worker 内、批量 worker 的整批任务中各只发生一次，
    而不是每个引擎一次。
    """

    return AutomationEngine(
        adb,
        screenshot_directory=screenshot_directory,
        log_callback=log_callback,
        progress_callback=progress_callback,
        ocr_client=ocr_client,
        ocr_boxes_client=ocr_client.recognize_with_boxes,
        screenshots_enabled=screenshots_enabled,
        log_foreground_package=bool(settings.get("log_foreground_package", False)),
    )


def reconnect_device(
    adb: AdbClient,
    log_callback: Callable[[str], None],
) -> bool:
    """重连所选的网络 ADB 设备。"""

    try:
        if adb.reconnect():
            log_callback("ADB 设备已重新连接")
            return True
    except Exception as exc:
        log_callback(f"ADB 设备重连异常: {exc}")
    return False


def _run_engine_attempt(
    engine: AutomationEngine,
    task: TaskDefinition,
    log: Callable[[str], None],
) -> RunResult:
    """单次执行尝试：引擎异常统一转为失败的 RunResult。"""

    try:
        return engine.run(task)
    except Exception as exc:
        log(f"任务异常: {exc}")
        return RunResult.failed(
            task.id,
            len(task.actions),
            failed_step="执行任务",
            error=str(exc),
        )


def _cleanup_apps_quietly(
    adb: AdbClient,
    settings: dict[str, Any],
    tasks: Sequence[TaskDefinition],
    log: Callable[[str], None],
) -> None:
    """清理目标 App；失败只记录，不中断收尾流程。"""

    try:
        cleanup_apps(adb, settings, tasks, log)
    except Exception as exc:
        log(f"App 清理失败: {exc}")


def _prune_outputs(
    settings: dict[str, Any],
    base_directory: Path,
    log_callback: Callable[[str], None],
) -> None:
    # 信任边界：清洗层保证 int/str；int() 是消费点断言——契约外脏类型显式
    # 抛错（TypeError/ValueError），不静默透传（与 number_setting 同风格）。
    mode = settings.get("cleanup_mode", "recycle")
    prune_files(
        resolve_path(settings.get("log_directory"), base_directory),
        int(settings.get("max_log_files", -1)),
        mode,
        log_callback,
    )
    prune_files(
        resolve_path(settings.get("screenshot_directory"), base_directory),
        int(settings.get("max_screenshot_files", -1)),
        mode,
        log_callback,
    )


def _shutdown_all(settings: dict[str, Any], log: LogCallback) -> None:
    """一并关闭 MuMu 实例及其配套 App。"""

    shutdown_mumu(settings, log)
    shutdown_mumu_app(settings, log)


class _WorkerBase(QObject):
    """TaskWorker / BatchTaskWorker 共享的字段与收尾流程。

    收尾（通知 / 清理）绝不允许吞掉 finished：任何异常都先记录，
    finished 一定发出，否则 GUI 会永久等待。
    """

    log_message = Signal(str)
    finished = Signal(object)

    def __init__(
        self,
        settings: dict[str, Any] | None = None,
        base_directory: Path | None = None,
        config_errors: tuple[TaskFileError, ...] = (),
    ):
        super().__init__()
        self.settings = settings or {}
        self.config_errors = tuple(config_errors)
        self.base_directory = base_directory or Path.cwd()
        self._stop_requested = Event()

    def _finish_with(
        self,
        outcome: RunResult | BatchRunResult,
        tasks: Sequence[TaskDefinition],
        *,
        notify: bool = True,
        label: str = "任务",
    ) -> None:
        """发送通知、清理输出文件并发出 finished。"""

        try:
            if notify:
                send_run_notification(
                    self.settings,
                    outcome,
                    tasks,
                    self.log_message.emit,
                    self.config_errors,
                )
            _prune_outputs(self.settings, self.base_directory, self.log_message.emit)
        except Exception as exc:
            self.log_message.emit(f"{label}收尾处理失败: {exc}")
        finally:
            self.finished.emit(outcome)


class TaskWorker(_WorkerBase):
    progress = Signal(int, int, str)

    def __init__(
        self,
        task: TaskDefinition,
        adb: AdbClient,
        screenshot_directory: Path,
        screenshots_enabled: bool = True,
        settings: dict[str, Any] | None = None,
        base_directory: Path | None = None,
        config_errors: tuple[TaskFileError, ...] = (),
        debug: bool = False,
    ):
        super().__init__(settings, base_directory, config_errors)
        self.task = task
        self.screenshots_enabled = screenshots_enabled
        self.debug = bool(debug)
        ocr_client = build_ocr_client(
            self.settings,
            self.base_directory,
            log_callback=self.log_message.emit,
        )
        self.engine = _build_engine(
            adb,
            screenshot_directory,
            self.settings,
            self.base_directory,
            self.log_message.emit,
            self.progress.emit,
            self.screenshots_enabled,
            ocr_client,
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
                _cleanup_apps_quietly(
                    self.engine.adb, self.settings, [self.task], self.log_message.emit
                )
                _shutdown_all(self.settings, self.log_message.emit)
            else:
                # 后续的连接超时或设备校验失败时，MuMu 可能已经启动，
                # 因此关闭流程仍需执行。
                _shutdown_all(self.settings, self.log_message.emit)
        if result is None:
            result = RunResult.failed(
                self.task.id,
                len(self.task.actions),
                error="设备准备失败",
            )
        self._finish_with(result, [self.task], notify=not self.debug)

    def _run_attempt(self) -> RunResult:
        return _run_engine_attempt(self.engine, self.task, self.log_message.emit)

    @Slot()
    def stop(self) -> None:
        self._stop_requested.set()
        self.engine.request_stop()


class BatchTaskWorker(_WorkerBase):
    """顺序执行配置的任务，并通过清理实现任务间隔离。"""

    task_started = Signal(str, int, int)
    progress = Signal(str, int, int, str)
    task_finished = Signal(object)

    def __init__(
        self,
        tasks: list[TaskDefinition] | tuple[TaskDefinition, ...],
        adb: AdbClient,
        screenshot_directory: Path,
        screenshots_enabled: bool = True,
        settings: dict[str, Any] | None = None,
        base_directory: Path | None = None,
        config_errors: tuple[TaskFileError, ...] = (),
    ):
        super().__init__(settings, base_directory, config_errors)
        self.tasks = tuple(tasks)
        self.adb = adb
        self.screenshot_directory = screenshot_directory
        self.screenshots_enabled = screenshots_enabled
        self._current_engine: AutomationEngine | None = None
        # OCR 会话初始化的代价是数秒级图优化；整批任务共享同一个 client，
        # 让 ONNX 推理会话只建立一次。
        self._ocr_client: OnnxOcrClient | None = None

    def _make_engine(self, task_id: str) -> AutomationEngine:
        if self._ocr_client is None:
            self._ocr_client = build_ocr_client(
                self.settings,
                self.base_directory,
                log_callback=self.log_message.emit,
            )

        def report_progress(index: int, total: int, description: str) -> None:
            self.progress.emit(task_id, index, total, description)

        return _build_engine(
            self.adb,
            self.screenshot_directory,
            self.settings,
            self.base_directory,
            self.log_message.emit,
            report_progress,
            self.screenshots_enabled,
            self._ocr_client,
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
                execution_count = task_execution_count(self.settings, task.id)
                if execution_count <= 0:
                    continue
                self.task_started.emit(task.id, index, len(self.tasks))
                self.log_message.emit(
                    f"开始任务 [{index}/{len(self.tasks)}]: {task.name}"
                )
                try:

                    def _attempt(task: TaskDefinition = task) -> RunResult:
                        return self._run_attempt(task)

                    result = run_task_executions(
                        task,
                        _attempt,
                        execution_count,
                        self.log_message.emit,
                        cleanup_callback=lambda: _cleanup_apps_quietly(
                            self.adb,
                            self.settings,
                            self.tasks,
                            self.log_message.emit,
                        ),
                        reconnect_callback=lambda: reconnect_device(
                            self.adb,
                            self.log_message.emit,
                        ),
                        stop_event=self._stop_requested,
                    )
                except Exception as exc:
                    self.log_message.emit(f"任务执行异常: {exc}")
                    result = RunResult.failed(
                        task.id,
                        len(task.actions),
                        failed_step="执行任务",
                        error=str(exc),
                    )
                _cleanup_apps_quietly(
                    self.adb, self.settings, self.tasks, self.log_message.emit
                )

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
        finally:
            _shutdown_all(self.settings, self.log_message.emit)

        # 准备阶段失败/停止且一个任务都没跑：为第一个任务补发结果，
        # 保证 task_started/task_finished/finished 的次序与正常流程一致。
        if not results and self.tasks and (stop_during_setup or setup_error):
            task = self.tasks[0]
            status = RunStatus.STOPPED if stop_during_setup else RunStatus.FAILED
            self._emit_prepare_failure(task, status, setup_error, results)

        stopped = (
            self._stop_requested.is_set()
            or stop_during_setup
            or any(result.status == RunStatus.STOPPED for result in results)
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
        self._finish_with(summary, self.tasks, label="批量任务")

    def _emit_prepare_failure(
        self,
        task: TaskDefinition,
        status: RunStatus,
        error: str | None,
        results: list[RunResult],
    ) -> None:
        """为 ``task`` 构造并发出准备阶段失败/停止的结果。

        结果会追加到 ``results``，且 ``task_started`` 先于
        ``task_finished`` 发出，与原先内联流程的次序一致。
        """

        if status == RunStatus.STOPPED:
            result = RunResult.stopped(task.id, len(task.actions))
        else:
            result = RunResult.failed(task.id, len(task.actions), error=error)
        results.append(result)
        self.task_started.emit(task.id, 1, len(self.tasks))
        self.task_finished.emit(result)

    def _run_attempt(self, task: TaskDefinition) -> RunResult:
        try:
            engine = self._make_engine(task.id)
            self._current_engine = engine
        except Exception as exc:
            self.log_message.emit(f"任务异常: {exc}")
            return RunResult.failed(
                task.id,
                len(task.actions),
                failed_step="执行任务",
                error=str(exc),
            )
        try:
            return _run_engine_attempt(engine, task, self.log_message.emit)
        finally:
            self._current_engine = None

    @Slot()
    def stop(self) -> None:
        self._stop_requested.set()
        if self._current_engine is not None:
            self._current_engine.request_stop()
