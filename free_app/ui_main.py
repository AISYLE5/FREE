"""主页与主窗口：页面控件、窗口容器与 GUI 入口。

``MainPage``：主页（任务列表 / 页头 / 设备状态 / 运行日志）。
``MainWindow``：窗口容器（窗口底色与固定尺寸、``QStackedWidget`` 三页切换、
``QThread`` + worker 运行编排、运行日志落盘、退出收尾）。
``main()`` / ``default_base_directory()``：GUI 入口。

界面层另外三个模块：``ui_common.py``（样式表 / 中文消息框 / 控件工厂 / 共享控件）、
``ui_settings.py``（设置页 + 设置对话框）、``ui_task_manager.py``（任务管理页 +
任务编辑器 + 查看器 + 动作参数表单）。
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO, cast

from PySide6.QtCore import QRectF, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDropEvent,
    QFont,
    QIcon,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .adb import AdbClient
from .background_task import BackgroundTask
from .config import (
    TaskFileError,
    ensure_settings_file,
    load_settings,
    load_task_directory,
    order_tasks,
    resolve_path,
    update_settings,
)
from .constants import SCREEN_DENSITY, SCREEN_HEIGHT, SCREEN_WIDTH
from .logging_utils import format_log_line
from .models import BatchRunResult, RunResult, RunStatus, TaskDefinition
from .mumu import MuMuError, mumu_adb_address_from_settings, resolve_adb_path
from .task_runner import batch_tasks_to_run, task_execution_count
from .ui_common import (
    MAIN_ACTION_BUTTON_WIDTH,
    MAIN_ICON_BUTTON_WIDTH,
    PAGE_BASE_QSS,
    ROW_BUTTON_HEIGHT,
    QMessageBox,
    confirm,
    refresh_style,
    secondary_button,
)
from .ui_settings import SettingsPage, create_embedded_dialog
from .ui_task_manager import TaskManagerPage
from .worker import BatchTaskWorker, TaskWorker

REFRESH_MIN_DISPLAY_SEC = 0.6

def build_app_icon() -> QIcon:
    """程序化绘制小尺寸多分辨率 FREE 图标，不依赖外部资源文件。"""

    icon = QIcon()
    for size in (16, 24, 32, 48, 64):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#137f73"))
        inset = max(1.0, size * 0.06)
        painter.drawRoundedRect(
            QRectF(inset, inset, size - inset * 2, size - inset * 2),
            size * 0.18,
            size * 0.18,
        )
        painter.setPen(QColor("#ffffff"))
        painter.setFont(
            QFont("Microsoft YaHei", max(8, int(size * 0.54)), QFont.Weight.Bold)
        )
        painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "F")
        painter.end()
        icon.addPixmap(pixmap)
    return icon

class _TaskList(QListWidget):
    order_changed = Signal()

    def dropEvent(self, event: QDropEvent) -> None:
        super().dropEvent(event)
        self.order_changed.emit()

class MainPage(QWidget):
    """主页控件；数据由窗口通过 ``set_*`` 方法推入，操作请求以信号发出。"""

    task_order_changed = Signal()
    start_current_requested = Signal()
    start_all_requested = Signal()
    stop_requested = Signal()
    refresh_requested = Signal()
    settings_requested = Signal()
    task_manager_requested = Signal()

    _UI_TASK_NAMES = {
        "bilibili_exp": "哔哩哔哩 · 经验",
        "bilibili_pts": "哔哩哔哩 · 积分",
        "bilibili_share": "哔哩哔哩 · 分享",
        "xiaoheihe": "小黑盒",
        "hanserclub": "毛怪俱乐部",
    }
    _STATE_LABELS = {
        "pending": "待执行",
        "running": "运行中",
        "success": "已完成",
        "failed": "失败",
        "stopped": "已停止",
        "skipped": "未执行",
    }
    _STATE_COLORS = {
        "pending": QColor("#617477"),
        "running": QColor("#087f73"),
        "success": QColor("#2b7b4e"),
        "failed": QColor("#b04843"),
        "stopped": QColor("#b06b2e"),
        "skipped": QColor("#8a9695"),
    }

    def __init__(self, *, make_adb: Callable[[], AdbClient]) -> None:
        super().__init__()
        self.setObjectName("appRoot")
        self._make_adb = make_adb
        self.tasks: list[TaskDefinition] = []
        self.task_by_id: dict[str, TaskDefinition] = {}
        self.task_states: dict[str, str] = {}
        self._refresh_active = False
        self._refresh_started = 0.0
        self._build_ui()
        self._apply_style()

    # ------------------------------------------------------------------ 构建

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 22, 0, 0)
        root_layout.setSpacing(14)
        root_layout.addLayout(self._build_header())

        task_panel = QFrame()
        task_panel.setObjectName("surface")
        # 与任务管理页的 280px 面板加其 2px 分隔条对齐。
        task_panel.setFixedWidth(282)
        task_layout = QVBoxLayout(task_panel)
        task_layout.setContentsMargins(14, 14, 14, 14)
        task_layout.setSpacing(8)

        task_heading = QHBoxLayout()
        task_heading.setSpacing(8)
        task_title = QLabel("任务顺序")
        task_title.setObjectName("taskSectionTitle")
        task_title.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
        )
        task_hint = QLabel("拖拽排序")
        task_hint.setObjectName("taskSortHint")
        task_hint.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
        )
        self.task_meta_label = QLabel()
        self.task_meta_label.setObjectName("mutedLabel")
        task_heading.addWidget(task_title)
        task_heading.addWidget(task_hint)
        task_heading.addStretch(1)
        task_heading.addWidget(self.task_meta_label)
        task_layout.addLayout(task_heading)

        self.task_list = _TaskList()
        self.task_list.setObjectName("taskList")
        self.task_list.setSpacing(7)
        self.task_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.task_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.task_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.task_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.task_list.setDragEnabled(True)
        self.task_list.setAcceptDrops(True)
        self.task_list.order_changed.connect(self.task_order_changed.emit)
        task_layout.addWidget(self.task_list, 1)

        # 右侧是纯日志面板：面板只包含日志视图，不再有状态徽标/进度区。
        log_panel = QFrame()
        log_panel.setObjectName("statusPanel")
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setFont(QFont("Microsoft YaHei", 9))
        log_layout.addWidget(self.log_view, 1)

        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter.setObjectName("contentSplitter")
        self.content_splitter.addWidget(task_panel)
        self.content_splitter.addWidget(log_panel)
        self.content_splitter.setHandleWidth(0)
        self.content_splitter.setSizes([282, 800])
        self.content_splitter.setCollapsible(0, False)
        self.content_splitter.setStretchFactor(0, 0)
        self.content_splitter.setStretchFactor(1, 1)
        root_layout.addWidget(self.content_splitter, 1)

    def _build_header(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(28, 0, 28, 0)
        layout.setSpacing(12)
        brand = QLabel("FREE")
        brand.setObjectName("brandLabel")
        brand.setFixedWidth(76)

        title_column = QVBoxLayout()
        title_column.setSpacing(1)
        title = QLabel("MuMu 自动化控制台")
        title.setObjectName("titleLabel")
        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("mutedLabel")
        title_column.addWidget(title)
        title_column.addWidget(self.subtitle_label)
        title_widget = QWidget()
        title_widget.setFixedWidth(210)
        title_widget.setLayout(title_column)

        self.device_label = QLabel("设备：检查中…")
        self.device_label.setObjectName("deviceBadge")
        self.device_label.setProperty("state", "checking")
        self.device_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.device_label.setFixedSize(216, 40)

        layout.addWidget(brand)
        layout.addWidget(title_widget)
        layout.addWidget(self.device_label)
        layout.addStretch(1)
        action_bar = QFrame()
        action_bar.setObjectName("mainActionBar")
        action_bar.setLayout(self._build_action_bar())
        # 固定总宽度，让头部的前导弹性区吸收"刷新"到
        # "刷新中…"的宽度增长，避免挤压按钮。
        action_bar.setFixedSize(640, 50)
        layout.addWidget(action_bar)
        return layout

    def _build_action_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        # 与任务管理页页头同一套按钮模板：只定宽、高度由样式表决定，
        # 四个按钮同宽，执行状态切换只改文字不改几何，整排不会跳动。
        self.task_manager_button = secondary_button(
            "任务管理", width=MAIN_ACTION_BUTTON_WIDTH
        )
        self.task_manager_button.clicked.connect(self.task_manager_requested.emit)

        self.run_all_button = secondary_button(
            "执行全部", width=MAIN_ACTION_BUTTON_WIDTH
        )
        self.run_all_button.setProperty("runState", "idle")
        self.run_all_button.clicked.connect(self.start_all_requested.emit)

        self.start_button = secondary_button(
            "执行", width=MAIN_ACTION_BUTTON_WIDTH
        )
        self.start_button.setProperty("runState", "idle")
        self.start_button.clicked.connect(self.start_current_requested.emit)

        self.refresh_button = secondary_button(
            "刷新", width=MAIN_ACTION_BUTTON_WIDTH
        )
        self.refresh_button.clicked.connect(self.refresh_requested.emit)

        self.settings_button = QToolButton()
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setText("⚙")
        # 齿轮是图标字符而非界面文字；保持其符号字体，
        # 这样它渲染为紧凑的单色图标，而不是回退字形。
        # 宽度固定；高度只设上限，实际高度与同排文字按钮一致（样式表给的行高）。
        self.settings_button.setFont(QFont("Segoe UI Symbol", 15))
        self.settings_button.setFixedWidth(MAIN_ICON_BUTTON_WIDTH)
        self.settings_button.setMaximumHeight(ROW_BUTTON_HEIGHT + 2)
        self.settings_button.clicked.connect(self.settings_requested.emit)

        layout.addStretch(1)
        layout.addWidget(self.task_manager_button)
        layout.addWidget(self.run_all_button)
        layout.addWidget(self.start_button)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.settings_button)
        return layout

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget#appRoot {
                background: #f3f7f5;
                color: #172b2c;
            }
            QFrame#surface {
                background: #ffffff;
                border: 1px solid #d9e5e1;
                border-radius: 0;
            }
            QFrame#mainActionBar {
                background: transparent;
                border: none;
                border-radius: 8px;
            }
            QLabel#brandLabel {
                color: #0b8478;
                font-size: 22px;
                font-weight: 800;
                padding-right: 5px;
            }
            QLabel#titleLabel {
                color: #172b2c;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#taskSectionTitle {
                color: #203637;
                font-size: 20px;
                font-weight: 750;
            }
            QLabel#taskSortHint {
                color: #8a9b98;
                font-size: 12px;
            }
            QLabel#mutedLabel {
                color: #718181;
                font-size: 11px;
            }
            QLabel#deviceBadge {
                min-width: 190px;
                padding: 8px 12px;
                border-radius: 6px;
                font-weight: 600;
            }
            QLabel#deviceBadge[state="ready"] {
                background: #e0f3ec;
                color: #126b5f;
                border: 1px solid #a5d8c8;
            }
            QLabel#deviceBadge[state="checking"] {
                background: #fff3d9;
                color: #95651b;
                border: 1px solid #edd39a;
            }
            QLabel#deviceBadge[state="error"] {
                background: #fde9e6;
                color: #a3403b;
                border: 1px solid #e8b8b3;
            }
            QToolButton#settingsButton {
                min-width: 48px;
                max-width: 48px;
                padding: 0;
                color: #35635f;
                background: #ffffff;
                border: 1px solid #cbdad6;
                border-radius: 6px;
            }
            QToolButton#settingsButton:hover {
                color: #0f746b;
                background: #e8f5f1;
                border-color: #83c0b5;
            }
            QToolButton#settingsButton:pressed {
                background: #dceee9;
                border-color: #65b3a7;
            }
            QListWidget#taskList {
                background: transparent;
                border: 0;
                outline: 0;
                padding: 2px 1px;
            }
            QListWidget#taskList::item {
                background: #f7faf9;
                border: 1px solid #e0e9e6;
                border-radius: 6px;
                padding: 11px 12px;
                color: #2a4243;
            }
            QListWidget#taskList::item:hover {
                background: #edf7f4;
                border-color: #a8d5cc;
            }
            QListWidget#taskList::item:selected {
                background: #dff2ed;
                border: 1px solid #65b3a7;
                color: #0f625b;
                outline: none;
            }
            QPushButton#secondaryButton[runState="busy"]:disabled {
                color: #7b8986;
                background: #e9eeec;
                border-color: #cbd6d2;
            }
            QPushButton#secondaryButton[runState="stop"] {
                color: #ffffff;
                background: #d6534d;
                border-color: #d6534d;
                font-weight: 700;
            }
            QPushButton#secondaryButton[runState="stop"]:hover {
                background: #bd403b;
                border-color: #bd403b;
            }
            QPushButton#secondaryButton[runState="stop"]:pressed {
                background: #a93632;
                border-color: #a93632;
            }
            QPushButton#secondaryButton[runState="stopping"]:disabled {
                color: #8c5f25;
                background: #fff0d9;
                border-color: #e4b56c;
                font-weight: 700;
            }
            QPlainTextEdit#logView {
                background: #f8fbfa;
                color: #344c4d;
                border: 1px solid #d7e5e1;
                border-radius: 0;
                padding: 9px;
                selection-background-color: #cfe9e2;
                selection-color: #183d3b;
            }
            QSplitter#contentSplitter::handle {
                background: transparent;
                width: 0px;
            }
            QStatusBar {
                background: #e6efeb;
                color: #5c6e6f;
                border-top: 1px solid #d4e1dc;
            }
            """
            + PAGE_BASE_QSS
        )

    # ------------------------------------------------------------ 数据显示

    def populate_tasks(self, *, select_first: bool) -> None:
        """按当前 ``tasks`` 重建任务列表。"""

        blocked = self.task_list.blockSignals(True)
        try:
            self.task_list.clear()
            self.task_meta_label.setText(f"{len(self.tasks)} 个任务")
            for task in self.tasks:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, task.id)
                item.setSizeHint(QSize(0, 70))
                self.task_list.addItem(item)
                self.update_task_item(task.id)
            if select_first and self.tasks:
                self.task_list.setCurrentRow(0)
        finally:
            self.task_list.blockSignals(blocked)

    def tasks_in_list(self) -> list[TaskDefinition]:
        """返回列表当前顺序对应的任务。"""

        ordered: list[TaskDefinition] = []
        for row in range(self.task_list.count()):
            task = self._task_from_item(self.task_list.item(row))
            if task is not None:
                ordered.append(task)
        return ordered

    def selected_task(self) -> TaskDefinition | None:
        return self._task_from_item(self.task_list.currentItem())

    def _task_from_item(self, item: QListWidgetItem | None) -> TaskDefinition | None:
        if item is None:
            return None
        return self.task_by_id.get(str(item.data(Qt.ItemDataRole.UserRole)))

    def display_task_name(self, task: TaskDefinition) -> str:
        return self._UI_TASK_NAMES.get(task.id, task.name)

    def update_task_item(self, task_id: str) -> None:
        for row in range(self.task_list.count()):
            item = self.task_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) != task_id:
                continue
            task = self.task_by_id[task_id]
            state = self.task_states.get(task_id, "pending")
            item.setText(
                f"{row + 1:02d}  {self.display_task_name(task)}\n"
                f"{self._STATE_LABELS.get(state, state)}  ·  {len(task.actions)} 个动作"
            )
            item.setForeground(
                self._STATE_COLORS.get(state, self._STATE_COLORS["pending"])
            )
            return

    def update_all_task_items(self) -> None:
        for task in self.tasks_in_list():
            self.update_task_item(task.id)

    def select_task_in_list(self, task_id: str) -> None:
        for row in range(self.task_list.count()):
            item = self.task_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == task_id:
                self.task_list.setCurrentRow(row)
                self.task_list.scrollToItem(item)
                return

    # ------------------------------------------------------------ 运行反馈

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)

    def set_execution_controls(self, state: str, run_mode: str | None) -> None:
        if state in ("running", "stopping"):
            (
                active_text,
                active_enabled,
                inactive_text,
                inactive_enabled,
                active_state,
                inactive_state,
            ) = {
                "running": ("停止", True, "执行", False, "stop", "busy"),
                "stopping": ("正在停止…", False, "执行", False, "stopping", "busy"),
            }[state]
            active = (
                self.run_all_button if run_mode == "batch" else self.start_button
            )
            inactive = (
                self.start_button if run_mode == "batch" else self.run_all_button
            )
            active.setText(active_text)
            active.setEnabled(active_enabled)
            inactive.setText(inactive_text)
            inactive.setEnabled(inactive_enabled)
        else:
            self.run_all_button.setText("执行全部")
            self.run_all_button.setEnabled(True)
            self.start_button.setText("执行")
            self.start_button.setEnabled(True)
            active_state = "idle"
            inactive_state = "idle"

        self.run_all_button.setProperty(
            "runState",
            active_state if run_mode == "batch" and state != "idle" else inactive_state,
        )
        self.start_button.setProperty(
            "runState",
            active_state if run_mode == "single" and state != "idle" else inactive_state,
        )
        for button in (self.run_all_button, self.start_button):
            refresh_style(button)

    def set_task_enabled(self, enabled: bool) -> None:
        self.task_list.setEnabled(enabled)

    def clear_log(self) -> None:
        self.log_view.clear()

    # ------------------------------------------------------------ 设备状态

    def set_device_status(
        self, state: str, text: str, *, refresh: bool = True
    ) -> None:
        self.device_label.setText(text)
        self.device_label.setProperty("state", state)
        if refresh:
            refresh_style(self.device_label)

    def mark_device_checking(self) -> None:
        self.set_device_status("checking", "设备：检查中…")

    def probe_device_status(self, settings: dict) -> tuple[str, str, str]:
        """在 GUI 线程之外执行：连接 MuMu 并探测设备状态。

        返回 ``(state, label_text, status_message)``；探测出现致命错误时
        抛出异常，由调用方呈现失败状态。
        """

        adb = self._make_adb()
        forwarded_address: str | None = None
        try:
            forwarded_address = mumu_adb_address_from_settings(settings)
        except (MuMuError, OSError, ValueError, TypeError):
            forwarded_address = None
        if forwarded_address:
            try:
                adb.connect(forwarded_address)
            except Exception:  # noqa: S110
                # 连接失败不致命：随后的设备列表探测会呈现真实状态。
                pass
        if not forwarded_address:
            raise MuMuError("MuMu 未返回动态 ADB 地址")
        devices = adb.list_devices()
        selected = next(
            (device for device in devices if device.serial == forwarded_address),
            None,
        )
        if selected and selected.state == "device":
            return "ready", f"MuMu · {selected.serial} · 可用", "MuMu ADB 设备可用"
        if selected:
            return (
                "error",
                f"MuMu · {selected.serial} · {selected.state}",
                "MuMu 设备当前不可用",
            )
        return "error", "MuMu · 未找到设备", "未找到可用 MuMu ADB 设备"

    # ------------------------------------------------------------ 刷新按钮

    def begin_refresh(self) -> None:
        self._refresh_active = True
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("刷新中…")

    def refresh_busy(self) -> bool:
        return self._refresh_active

    def mark_refresh_started(self, timestamp: float) -> None:
        self._refresh_started = timestamp

    def refresh_started_at(self) -> float:
        return self._refresh_started

    def restore_refresh_button(self) -> None:
        self._refresh_active = False
        self.refresh_button.setText("刷新")
        self.refresh_button.setEnabled(True)

    def set_refresh_enabled(self, enabled: bool) -> None:
        self.refresh_button.setEnabled(enabled)

# 任务显示名映射：窗口日志与页面共用同一份表。
UI_TASK_NAMES = MainPage._UI_TASK_NAMES

def task_display_name(task: TaskDefinition) -> str:
    """任务显示名（不依赖页面实例，供窗口日志复用）。"""

    return UI_TASK_NAMES.get(task.id, task.name)

class MainWindow(QMainWindow):

    def __init__(self, base_directory: Path):

        super().__init__()

        self.base_directory = base_directory

        self.settings_path = base_directory / "config" / "settings.json"

        self.tasks_directory = base_directory / "config" / "tasks"

        ensure_settings_file(self.settings_path)

        self.settings = load_settings(self.settings_path)

        raw_tasks, config_errors = load_task_directory(

            self.tasks_directory,

            variables={"qq_group_name": self.settings.get("qq_group_name", "")},

        )

        self.config_errors = list(config_errors)

        self.tasks = order_tasks(raw_tasks, self.settings.get("task_order"))

        self.task_by_id = {task.id: task for task in self.tasks}

        self.worker_thread: QThread | None = None

        self.worker: TaskWorker | BatchTaskWorker | None = None

        self.run_mode: str | None = None

        self.active_tasks: list[TaskDefinition] = []

        self.task_states = {task.id: "pending" for task in self.tasks}

        self.task_results: dict[str, RunResult] = {}

        self.task_executions_done = 0

        self.log_file: TextIO | None = None

        self._closing = False

        self._device_task: BackgroundTask | None = None

        self._device_finalize_refresh = False

        # 两个内嵌编辑器首次打开时才创建；类型由各自的 ui_* 页面模块负责。

        self._settings_widget: Any = None

        self._task_manager_widget: Any = None

        # 日志缓冲:高频日志(OCR 候选/进度)只在定时器触发时批量刷新 UI 与磁盘。

        self._log_pending: list[str] = []

        self._log_timer = QTimer(self)

        self._log_timer.setInterval(50)

        self._log_timer.timeout.connect(self._flush_log_queue)

        app_icon = build_app_icon()

        self.setWindowIcon(app_icon)

        application = QApplication.instance()

        if application is not None:
            application.setWindowIcon(app_icon)

        self.setWindowTitle("FREE · MuMu 自动化控制台")

        self.setFixedSize(1240, 820)

        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)

        self._build_ui()

        self._populate_tasks()

        self._report_config_errors(self.config_errors, popup=False)

        self._update_subtitle()

        self._update_device_status()

    def __getattr__(self, name: str) -> Any:

        """把页面控件/方法的访问转发到对应的页面控件。

        仅当常规属性查找失败时触发（实例属性、类方法与 Qt 属性优先），

        因此不会遮蔽 ``MainWindow`` 自己的成员。

        """

        for page_name in ("_main_page", "_task_manager_page", "_settings_page"):

            page = self.__dict__.get(page_name)

            if page is not None and hasattr(page, name):

                return getattr(page, name)

        raise AttributeError(name)

    # ------------------------------------------------------------------ 构建

    def _thread_active(self) -> bool:

        return self.worker_thread is not None

    def _build_ui(self) -> None:

        self._task_manager_page = TaskManagerPage(

            self.settings_path,

            self.base_directory,

            self._task_manager_back,

        )

        self._main_page = MainPage(

            # 传"取 ADB 客户端"的回调而非绑定方法：每次探测都重新解析，

            # 使实例级替换（测试/调试）在后台线程里同样生效。

            make_adb=lambda: self._make_adb(),

        )

        self._settings_page = SettingsPage(self._close_settings_page)

        self._wire_main_page_signals()

        self._wire_task_manager_signals()

        self.main_page = self._main_page

        self.settings_page = self._settings_page

        self.task_manager_page = self._task_manager_page

        self.pages = QStackedWidget()

        self.pages.setObjectName("appPages")

        self.pages.addWidget(self.main_page)

        self.pages.addWidget(self.settings_page)

        self.pages.addWidget(self.task_manager_page)

        self.setCentralWidget(self.pages)

        self.setStatusBar(QStatusBar())

        self.statusBar().hide()

        self.statusBar().showMessage("就绪")

        # 窗口自身的底色（页头上下留白、状态栏底部）不属于任何页面，

        # 必须留在窗口的样式表里；页面只负责 ``appRoot`` 内容区。

        self.setStyleSheet(

            "QMainWindow { background: #f3f7f5; color: #172b2c; }"

        )

        splitter_handle = self._main_page.content_splitter.handle(1)

        if splitter_handle is not None:

            splitter_handle.setCursor(Qt.CursorShape.ArrowCursor)

            splitter_handle.setEnabled(False)

    def _wire_main_page_signals(self) -> None:

        page = self._main_page

        page.task_order_changed.connect(self._save_task_order)

        page.start_current_requested.connect(self._start_current_task)

        page.start_all_requested.connect(self._start_all_tasks)

        page.stop_requested.connect(self._stop_task)

        page.refresh_requested.connect(self._refresh_all)

        page.settings_requested.connect(self._open_settings)

        page.task_manager_requested.connect(self._open_task_manager)

    def _wire_task_manager_signals(self) -> None:

        page = self._task_manager_page

        page.tasks_changed.connect(self._on_task_manager_changed)

        page.feedback_requested.connect(self._show_task_manager_feedback)

        page.run_action_requested.connect(self._run_single_action_from_manager)

        page.run_stop_requested.connect(self._stop_task)

        page.pointer_location_failed.connect(self._revert_pointer_toggle)

    # ------------------------------------------------------------ 任务列表

    def _populate_tasks(self) -> None:

        self._main_page.tasks = self.tasks

        self._main_page.task_by_id = self.task_by_id

        self._main_page.task_states = self.task_states

        self._main_page.populate_tasks(select_first=True)

    def _display_task_name(self, task: TaskDefinition) -> str:

        return task_display_name(task)

    def _tasks_in_list(self) -> list[TaskDefinition]:

        return self._main_page.tasks_in_list()

    def _update_task_item(self, task_id: str) -> None:

        self._main_page.update_task_item(task_id)

    def _update_all_task_items(self) -> None:

        self._main_page.update_all_task_items()

    def _select_task_in_list(self, task_id: str) -> None:

        self._main_page.select_task_in_list(task_id)

    def _selected_task(self) -> TaskDefinition | None:

        return self._main_page.selected_task()

    def _reload_tasks(self) -> bool:

        """重新扫描任务目录并重建任务状态。

        目录无法加载时返回 False。

        """

        try:

            raw_tasks, config_errors = load_task_directory(

                self.tasks_directory,

                variables={"qq_group_name": self.settings.get("qq_group_name", "")},

            )

        except (OSError, ValueError) as exc:

            QMessageBox.warning(self, "任务加载失败", str(exc))

            return False

        self.config_errors = list(config_errors)

        self.tasks = order_tasks(raw_tasks, self.settings.get("task_order"))

        self.task_by_id = {task.id: task for task in self.tasks}

        self.task_states = {task.id: "pending" for task in self.tasks}

        self.task_results = {}

        return True

    def _report_config_errors(

        self, errors: list[TaskFileError], *, popup: bool

    ) -> None:

        if not errors:

            return

        for error in errors:

            self._append_log(f"{error.path.name}已损坏，跳过该任务：{error.reason}")

        if popup:

            QMessageBox.warning(

                self,

                "任务配置错误",

                "\n".join(f"{error.path.name}已损坏，跳过该任务" for error in errors),

            )

        else:

            self.statusBar().showMessage(

                f"已跳过 {len(errors)} 个损坏的任务文件，详见日志"

            )

    def _resync_tasks_and_ui(self, popup: bool) -> bool:

        """重载任务、重建列表并报告配置错误。

        任务目录无法加载时返回 False；此时任务状态保持不变，

        由调用方自行处理失败。

        """

        if not self._reload_tasks():

            return False

        self._populate_tasks()

        self._report_config_errors(self.config_errors, popup=popup)

        return True

    @Slot()

    def _refresh_tasks(self) -> None:

        if self.worker_thread:

            return

        if not self._resync_tasks_and_ui(popup=True):

            return

        if not self.config_errors:

            self.statusBar().showMessage("任务列表已刷新")

    @Slot()

    def _refresh_all(self) -> None:

        self._refresh_tasks()

        self._refresh_device()

    @Slot()

    def _save_task_order(self) -> None:

        if self.worker_thread:

            return

        task_order = [task.id for task in self._tasks_in_list()]

        try:

            self.settings = update_settings(

                self.settings_path, {"task_order": task_order}

            )

            self._update_all_task_items()

            self.statusBar().showMessage("任务顺序已自动保存")

        except (OSError, ValueError) as exc:

            QMessageBox.warning(self, "保存失败", f"无法保存任务顺序：{exc}")

    # ------------------------------------------------------------ 设备状态

    def _make_adb(self) -> AdbClient:

        return AdbClient(

            executable=resolve_adb_path(self.settings),

            command_timeout=float(self.settings.get("command_timeout_seconds", 10)),

        )

    @Slot()

    def _refresh_device(self) -> None:

        if self._main_page.refresh_busy():

            return

        self._main_page.begin_refresh()

        self._main_page.mark_refresh_started(time.monotonic())

        self._update_device_status(finalize_refresh=True)

    def _finalize_refresh_button(self) -> None:

        remaining = REFRESH_MIN_DISPLAY_SEC - (

            time.monotonic() - self._main_page.refresh_started_at()

        )

        delay_ms = max(0, int(remaining * 1000))

        QTimer.singleShot(delay_ms, self._restore_refresh_button)

    def _restore_refresh_button(self) -> None:

        self._main_page.restore_refresh_button()

    def _update_device_status(self, finalize_refresh: bool = False) -> None:

        if self._device_task is not None and self._device_task.isRunning():

            return

        self._device_finalize_refresh = finalize_refresh

        self._main_page.mark_device_checking()

        task = BackgroundTask(self._probe_device_status)

        task.succeeded.connect(self._apply_device_status)

        task.failed.connect(self._apply_device_status_failure)

        # 先清引用再 deleteLater：残留引用会让后续 isRunning() 访问已销毁的 C++ 对象。

        task.finished.connect(self._on_device_task_finished)

        task.finished.connect(task.deleteLater)

        self._device_task = task

        task.start()

    @Slot()

    def _on_device_task_finished(self) -> None:

        self._device_task = None

    def _probe_device_status(self) -> tuple[str, str, str]:

        """在 GUI 线程之外执行：连接 MuMu 并探测设备状态。"""

        return self._main_page.probe_device_status(self.settings)

    @Slot(object)

    def _apply_device_status(self, result: object) -> None:

        state, text, message = cast(tuple[str, str, str], result)

        self._set_device_status(state, text, message)

    @Slot(str)

    def _apply_device_status_failure(self, message: str) -> None:

        self._set_device_status("error", "MuMu · ADB 不可用", message)

    def _set_device_status(

        self, state: str, text: str, message: str | None = None

    ) -> None:

        self._main_page.set_device_status(state, text, refresh=False)

        if message:

            self.statusBar().showMessage(message)

        refresh_style(self.device_label)

        if self._device_finalize_refresh:

            self._finalize_refresh_button()

    # ------------------------------------------------------------------ 日志

    @Slot(str)

    def _append_log(self, message: str) -> None:

        self._log_pending.append(format_log_line(message))

        # 定时批量刷新:任务运行期每个 ADB 命令会产生多行日志,

        # 逐行 appendPlainText+滚动+flush 会让 UI 线程每行做三件 IO 级工作。

        if not self._log_timer.isActive():

            self._log_timer.start()

    def _flush_log_queue(self) -> None:

        pending = self._log_pending

        if not pending:

            return

        self._log_pending = []

        log_view = self.log_view

        scroll_bar = log_view.verticalScrollBar()

        # 只有本来就接近底部时才跟随滚动,避免用户上翻查看时被拉走。

        follow_bottom = scroll_bar.value() >= scroll_bar.maximum() - 8

        for line in pending:

            log_view.appendPlainText(line)

        joined = "\n".join(pending)

        if self.log_file:

            self.log_file.write(joined + "\n")

            self.log_file.flush()

        if follow_bottom:

            scroll_bar.setValue(scroll_bar.maximum())

    # ------------------------------------------------------------ 任务执行

    def _prepare_run(

        self,

        tasks: list[TaskDefinition],

        *,

        log_receiver: Callable[[str], None] | None = None,

        progress_receiver: Callable[[int, int, str], None] | None = None,

        finished_receiver: Callable[[RunResult], None] | None = None,

    ) -> bool:

        if self.worker_thread or not tasks:

            return False

        try:

            adb = self._make_adb()

        except Exception as exc:

            QMessageBox.warning(self, "设备不可用", str(exc))

            self._update_device_status()

            return False

        debug_mode = self.run_mode == "debug"

        append_log = log_receiver or self._append_log

        log_directory = resolve_path(

            self.settings.get("log_directory"), self.base_directory

        )

        screenshot_directory = resolve_path(

            self.settings.get("screenshot_directory"), self.base_directory

        )

        max_log_files = self.settings.get("max_log_files", -1)

        log_path: Path | None = None

        if debug_mode:

            log_path = None

            self.log_file = None

        elif max_log_files != 0:

            log_directory.mkdir(parents=True, exist_ok=True)

            log_path = log_directory / f"run_{datetime.now():%Y%m%d_%H%M%S}.log"

            self.log_file = log_path.open("w", encoding="utf-8")

        else:

            self.log_file = None

        if not debug_mode:

            self._main_page.clear_log()

        self.active_tasks = tasks

        self.task_results = {}

        self.task_executions_done = 0

        self.task_states = {task.id: "pending" for task in self.tasks}

        self._update_all_task_items()

        append_log(

            f"开始执行 {len(tasks)} 个任务：{' → '.join(self._display_task_name(task) for task in tasks)}"

        )

        if log_path is not None:

            append_log(f"日志文件: {log_path}")

        if debug_mode:

            append_log("调试模式：直接连接已运行实例，不启动 MuMu、不清理 App")

        else:

            append_log(

                "运行配置: "

                f"auto_start_mumu={self.settings.get('auto_start_mumu', True)}, "

                f"close_mumu_after_run={self.settings.get('close_mumu_after_run', False)}, "

                f"close_mumu_app_after_run="

                f"{self.settings.get('close_mumu_app_after_run', False)}, "

                f"task_execution_counts={self.settings.get('task_execution_counts', {})}, "

                f"cleanup_after_task={self.settings.get('cleanup_after_task', True)}"

            )

        self.worker_thread = QThread(self)

        if self.run_mode == "batch":

            batch_worker = BatchTaskWorker(

                tasks,

                adb=adb,

                screenshot_directory=screenshot_directory,

                screenshots_enabled=self._effective_screenshots_enabled(),

                settings=self.settings,

                base_directory=self.base_directory,

                config_errors=tuple(self.config_errors),

            )

            batch_worker.task_started.connect(self._batch_task_started)

            batch_worker.progress.connect(self._batch_progress)

            batch_worker.task_finished.connect(self._batch_task_finished)

            batch_worker.finished.connect(self._batch_finished)

            worker: TaskWorker | BatchTaskWorker = batch_worker

        else:

            worker = TaskWorker(

                tasks[0],

                adb=adb,

                screenshot_directory=screenshot_directory,

                screenshots_enabled=self._effective_screenshots_enabled(),

                settings=self.settings,

                base_directory=self.base_directory,

                config_errors=tuple(self.config_errors),

                debug=debug_mode,

            )

            worker.progress.connect(self._single_progress)

            worker.finished.connect(self._single_finished)

            if progress_receiver is not None:

                worker.progress.connect(progress_receiver)

            if finished_receiver is not None:

                worker.finished.connect(finished_receiver)

        self.worker = worker

        self.worker.log_message.connect(append_log)

        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)

        self.worker.finished.connect(self.worker_thread.quit)

        self.worker_thread.finished.connect(self._thread_finished)

        self.worker_thread.finished.connect(self.worker.deleteLater)

        self.worker_thread.start()

        self._main_page.set_task_enabled(False)

        self._set_execution_controls("running")

        self._main_page.set_refresh_enabled(False)

        self._main_page.set_device_status("checking", "MuMu · 正在启动/连接")

        self.statusBar().showMessage("任务运行中")

        return True

    @Slot()

    def _start_current_task(self) -> None:

        if self.worker_thread:

            self._stop_task()

            return

        task = self._selected_task()

        if task is None:

            QMessageBox.warning(self, "没有任务", "请先选择一个任务。")

            return

        self.run_mode = "single"

        self._prepare_run([task])

    @Slot()

    def _start_all_tasks(self) -> None:

        if self.worker_thread:

            self._stop_task()

            return

        self._save_task_order()

        all_tasks = self._tasks_in_list()

        if not all_tasks:

            QMessageBox.warning(self, "没有任务", "配置中没有可执行任务。")

            return

        tasks = batch_tasks_to_run(all_tasks, self.settings)

        skipped_tasks = [

            task

            for task in all_tasks

            if task_execution_count(self.settings, task.id) == 0

        ]

        if not tasks:

            QMessageBox.warning(

                self, "没有任务", "全部任务的执行次数均为 0，没有可执行任务。"

            )

            return

        self.run_mode = "batch"

        if self._prepare_run(tasks):

            for task in skipped_tasks:

                self.task_states[task.id] = "skipped"

                self._update_task_item(task.id)

            if skipped_tasks:

                self._append_log(

                    "跳过执行次数为 0 的任务："

                    + "、".join(self._display_task_name(task) for task in skipped_tasks)

                )

    @Slot()

    def _stop_task(self) -> None:

        if self.worker:

            self.worker.stop()

            self.statusBar().showMessage("正在停止当前任务…")

            self._set_execution_controls("stopping")

    @Slot(int, int, str)

    def _single_progress(self, index: int, total: int, description: str) -> None:

        """单任务逐动作进度：进度区已移除，这里保留信号槽（含进度接收方转发）。

        运行进度仍会写入运行日志（由 worker 自己输出），并在任务管理页的

        调试运行视图里显示；主页不再有进度控件可更新。

        """

        del index, total, description

    @Slot(str, int, int)

    def _batch_task_started(self, task_id: str, index: int, total: int) -> None:

        del index, total  # 进度区已移除，仅在任务列表上标记状态与选中项。

        self.task_states[task_id] = "running"

        self._update_task_item(task_id)

        self._select_task_in_list(task_id)

    @Slot(str, int, int, str)

    def _batch_progress(

        self, task_id: str, index: int, total: int, description: str

    ) -> None:

        del index, total, description  # 逐动作进度不再展示，保留选中项跟随。

        self._select_task_in_list(task_id)

    @Slot(object)

    def _batch_task_finished(self, result: RunResult) -> None:

        self.task_results[result.task_id] = result

        self.task_executions_done += 1

        self.task_states[result.task_id] = result.status.value

        self._update_task_item(result.task_id)

    @Slot(object)

    def _single_finished(self, result: RunResult) -> None:

        self.task_results[result.task_id] = result

        self.task_states[result.task_id] = result.status.value

        self._update_task_item(result.task_id)

        self._show_run_status(result.status, result.error, result.failed_step)

    @Slot(object)

    def _batch_finished(self, result: BatchRunResult) -> None:

        # 没有结果的任务（批量准备失败/中止时未轮到执行）统一标记为跳过，

        # 避免任务列表残留“待执行”。

        for task in self.active_tasks:

            if task.id not in self.task_results:

                self.task_states[task.id] = "skipped"

                self._update_task_item(task.id)

        detail = (

            f"失败任务：{result.failed_task}" if result.failed_task else result.error

        )

        self._show_run_status(result.status, detail, result.failed_task)

    def _show_run_status(

        self, status: RunStatus, error: str | None, failed_step: str | None

    ) -> None:

        """任务收尾：只写状态栏消息（主页已无状态/进度控件）。"""

        if status == RunStatus.SUCCESS:

            message = (

                "任务完成，调试模式未关闭 App"

                if self.run_mode == "debug"

                else "任务完成，App 进程已关闭"

            )

        elif status == RunStatus.STOPPED:

            message = (

                "任务已停止，调试模式未清理 App"

                if self.run_mode == "debug"

                else "任务已停止，未启动后续任务"

            )

        else:

            message = error or (f"任务失败：{failed_step}" if failed_step else "任务失败")

        self.statusBar().showMessage(message)

    @Slot()

    def _thread_finished(self) -> None:

        self._flush_log_queue()

        if self.log_file:

            self.log_file.close()

            self.log_file = None

        if self.worker_thread:

            self.worker_thread.deleteLater()

        self.worker_thread = None

        self.worker = None

        self.run_mode = None

        self._main_page.set_task_enabled(True)

        self._set_execution_controls("idle")

        self._main_page.set_refresh_enabled(True)

        self._update_device_status()

        if self._closing:

            # 窗口关闭等待任务停止时,由这里接力完成真正的退出流程。

            self.close()

    # ------------------------------------------------------- 页面间共享状态

    def _update_subtitle(self) -> None:

        vmindex = self.settings.get("mumu_vm_index", 0)

        text = (

            f"实例 {vmindex}  ·  {SCREEN_WIDTH}×{SCREEN_HEIGHT}  ·  {SCREEN_DENSITY} dpi"

        )

        main_page = self.__dict__.get("_main_page")

        if main_page is not None:

            main_page.set_subtitle(text)

        else:

            # 兼容只在窗口上挂 ``subtitle_label`` 的轻量替身。

            self.subtitle_label.setText(text)

    def _effective_screenshots_enabled(self) -> bool:

        # 清洗层保证 int；max_screenshot_files=0 时完全不保存截图。

        return self.settings.get("max_screenshot_files", -1) != 0

    def _set_execution_controls(self, state: str) -> None:

        self._main_page.set_execution_controls(state, self.run_mode)

    @staticmethod

    def _refresh_style(widget: QWidget) -> None:

        refresh_style(widget)

    # ------------------------------------------------------------ 设置页面

    @Slot()

    def _open_settings(self) -> None:

        if self.worker_thread:

            self.statusBar().showMessage("任务运行中，请先停止任务。")

            return

        if self._settings_widget is None:

            dialog = create_embedded_dialog(

                self.settings_path, self, self.base_directory

            )

            self._settings_widget = dialog

            self._settings_page.add_dialog(dialog)

            dialog.log_message.connect(self._append_log)

            dialog.ocr_test_finished.connect(

                lambda: self.pages.setCurrentWidget(self.main_page)

            )

            dialog.accepted.connect(self._on_settings_saved)

            dialog.rejected.connect(self._on_settings_back)

        self._settings_widget.refresh_values()

        self.pages.setCurrentWidget(self.settings_page)

        self._settings_widget.show()

    def _close_settings_page(self) -> None:

        if self._settings_widget is not None:

            self._settings_widget.reject()

    def _on_settings_saved(self) -> None:

        self.settings = load_settings(self.settings_path)

        self._resync_tasks_and_ui(popup=False)

        self._update_subtitle()

        self._append_log("设置已保存")

        self.statusBar().showMessage("设置已保存")

        self.pages.setCurrentWidget(self.main_page)

    def _on_settings_back(self) -> None:

        self.pages.setCurrentWidget(self.main_page)

    # -------------------------------------------------------- 任务管理页面

    @Slot()

    def _open_task_manager(self) -> None:

        if self.worker_thread:

            self.statusBar().showMessage("任务运行中，请先停止任务。")

            return

        self._task_manager_widget = self._task_manager_page.ensure_widget()

        self._task_manager_page.reload()

        self.pages.setCurrentWidget(self.task_manager_page)

        self._task_manager_page.preload()

    def _on_task_manager_pointer_toggled(self, enabled: bool) -> None:

        self._task_manager_page.set_pointer_location(enabled)

    def _revert_pointer_toggle(self, enabled: bool) -> None:

        """后台切换坐标显示失败时回退按钮状态。"""

        self._task_manager_page.revert_pointer_toggle(enabled)

        self.statusBar().showMessage("指针坐标设置失败，请检查 ADB 连接。")

    def _close_task_manager_page(self) -> None:

        self.pages.setCurrentWidget(self.main_page)

    def _show_task_manager_feedback(self, message: str) -> None:

        self._task_manager_page.show_copy_feedback(message)

    def _hide_task_manager_copy_feedback(self) -> None:

        self._task_manager_page.hide_copy_feedback()

    def _task_manager_back(self) -> None:

        if self._task_manager_page.go_back():

            return

        self._close_task_manager_page()

    def _on_task_manager_changed(self) -> None:

        self.settings = load_settings(self.settings_path)

        if not self._resync_tasks_and_ui(popup=False):

            self.statusBar().showMessage("任务加载失败，未刷新任务列表。")

            return

        self._update_subtitle()

    @Slot(list, str, str)

    def _run_single_action_from_manager(

        self,

        actions: list[dict],

        package: str,

        task_name: str,

    ) -> None:

        """把任务管理页发来的动作作为一次性调试任务运行。"""

        if self.worker_thread:

            QMessageBox.warning(self, "任务运行中", "请先停止当前运行的任务。")

            return

        from .models import Action

        try:

            parsed_actions = tuple(Action.from_dict(action) for action in actions)

        except Exception as exc:

            QMessageBox.warning(self, "动作解析失败", str(exc))

            return

        if not parsed_actions:

            QMessageBox.warning(self, "无法运行动作", "动作列表为空。")

            return

        temp_task = TaskDefinition(

            id="_single_action_test",

            name=task_name,

            package=package,

            actions=parsed_actions,

        )

        manager = self._task_manager_widget

        if manager is None:

            return

        manager.show_run_viewer(task_name)

        self.run_mode = "debug"

        if not self._prepare_run(

            [temp_task],

            log_receiver=manager.append_run_log,

            progress_receiver=manager.set_run_progress,

            finished_receiver=manager.finish_run,

        ):

            self.run_mode = None

            manager.abort_run("未能启动调试运行。")

            return

    # ------------------------------------------------------------------ 退出

    def _confirm_exit_with_unsaved_manager_changes(self) -> bool:

        manager = self._task_manager_widget

        if manager is None or not manager.has_unsaved_changes():

            return True

        return confirm(self, "未保存的修改", "任务管理器中有未保存的修改，确定退出吗？")

    def _confirm_exit_with_unsaved_settings_changes(self) -> bool:

        settings_widget = self._settings_widget

        if settings_widget is None or not settings_widget.has_unsaved_changes():

            return True

        return confirm(self, "未保存的修改", "设置中有未保存的修改，确定退出吗？")

    def closeEvent(self, event: QCloseEvent) -> None:

        if not self._confirm_exit_with_unsaved_manager_changes():

            event.ignore()

            return

        if not self._confirm_exit_with_unsaved_settings_changes():

            event.ignore()

            return

        if self.worker_thread:

            # 不再 while-wait 忙等(它不处理事件,statusBar 提示永远画不出来,

            # 且 worker 卡在 10s 超时 ADB 命令时窗口白屏)。

            # 改为:请求停止 → 忽略本次关闭事件 → _thread_finished 在工作线程

            # 退出后调用 self.close() 走完整收尾。

            self._stop_task()

            self._closing = True

            self.statusBar().showMessage("正在停止任务并关闭…")

            event.ignore()

            return

        if self._settings_widget is not None:

            self._settings_widget.shutdown_downloads()

        if self._task_manager_widget is not None:

            self._task_manager_widget.shutdown()

        if self._device_task is not None and self._device_task.isRunning():

            # ADB 探测自带超时，这里给足余量避免销毁运行中的 QThread。

            self._device_task.wait(30000)

        self._flush_log_queue()

        if self.log_file:

            self.log_file.close()

            self.log_file = None

        event.accept()

def create_window(base_directory: Path) -> MainWindow:

    return MainWindow(base_directory)

def default_base_directory() -> Path:
    """返回项目根目录——用户配置目录唯一支持的基准目录。"""

    return Path(__file__).resolve().parent.parent

def main(argv: Sequence[str] | None = None) -> int:
    """GUI 入口：建 QApplication、设中文字体、显示主窗口。"""

    del argv  # 仅 GUI 入口；不解释命令行参数。
    base_directory = default_base_directory()

    from PySide6.QtGui import QFont

    application = QApplication(sys.argv)
    application.setApplicationName("FREE")
    application.setFont(QFont("Microsoft YaHei", 9))
    window = create_window(base_directory)
    window.show()
    return application.exec()

if __name__ == "__main__":
    raise SystemExit(main())

if __name__ == "__main__":

    raise SystemExit(main())
