"""Qt GUI 层的一次性后台任务辅助。

在专用的 QThread 上运行 callable，并通过信号把结果送回所属（GUI）线程，
避免 ADB、OCR、SMTP 等阻塞操作卡死事件循环。实现刻意保持极简：直接把
QThread 自身的 ``run()`` 接到目标函数，调用点无需再做 moveToThread。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal


class BackgroundTask(QThread):
    """在新线程上运行一次 ``target()``。

    发出 ``succeeded(result)`` 或 ``failed(message)`` 后线程随即结束。
    派发方会持有引用直到 ``finished``；若运行中任务的属主可能被销毁
    （窗口关闭），调用方必须 ``wait()``。
    """

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self, target: Callable[[], Any], parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._target = target

    def run(self) -> None:
        try:
            self.succeeded.emit(self._target())
        except Exception as exc:
            self.failed.emit(str(exc))


class BackgroundTaskOwner:
    """Mixin：批量派发 :class:`BackgroundTask` 并在退出前统一等待。

    混入者持有任务引用直到 ``finished``，防止运行中的线程被垃圾回收；
    窗口关闭时调用 :meth:`wait_background_tasks` 收尾。
    """

    _background_tasks: list[BackgroundTask]

    def _init_background_tasks(self) -> None:
        self._background_tasks = []

    def spawn_background(
        self,
        target: Callable[[], Any],
        on_success: Callable[[Any], object] | None = None,
        on_failure: Callable[[str], object] | None = None,
    ) -> BackgroundTask:
        """在 GUI 线程之外运行 ``target()``；结果经回调送达。"""

        task = BackgroundTask(target)
        if on_success is not None:
            task.succeeded.connect(on_success)
        if on_failure is not None:
            task.failed.connect(on_failure)

        def _forget() -> None:
            if task in self._background_tasks:
                self._background_tasks.remove(task)

        task.finished.connect(_forget)
        self._background_tasks.append(task)
        task.start()
        return task

    def wait_background_tasks(self, timeout_ms: int = 8000) -> None:
        for task in list(self._background_tasks):
            task.wait(timeout_ms)
