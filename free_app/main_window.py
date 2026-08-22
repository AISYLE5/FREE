from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Callable, TextIO

from PySide6.QtCore import QPoint, QThread, Qt, QSize, QTimer, QRectF, Signal, Slot
from PySide6.QtGui import QCloseEvent, QColor, QDropEvent, QFont, QIcon, QPainter, QPixmap
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
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolTip,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .adb import AdbClient
from .config import (
    TaskFileError,
    ensure_settings_file,
    load_json,
    load_settings,
    load_task_directory,
    order_tasks,
    resolve_path,
    save_settings,
)
from .constants import SCREEN_DENSITY, SCREEN_HEIGHT, SCREEN_WIDTH
from .logging_utils import format_log_line
from .message_box import QMessageBox
from .models import BatchRunResult, RunResult, RunStatus, TaskDefinition
from .mumu import MuMuError, mumu_adb_address_from_settings
from .settings_dialog import SettingsDialog
from .task_manager import TaskManagerWidget
from .task_runner import batch_tasks_to_run, task_execution_count
from .worker import BatchTaskWorker, TaskWorker
from . import styles as _s

# Minimum time the "刷新" button stays in its busy state before re-enabling.
_REFRESH_MIN_DISPLAY_SEC = 0.6


def build_app_icon() -> QIcon:
    """Create a small multi-resolution FREE icon without an external asset."""

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
        painter.setFont(QFont("Microsoft YaHei", max(8, int(size * 0.54)), QFont.Weight.Bold))
        painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "F")
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class _TaskList(QListWidget):
    order_changed = Signal()

    def dropEvent(self, event: QDropEvent) -> None:
        super().dropEvent(event)
        self.order_changed.emit()


class MainWindow(QMainWindow):
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
        self._refresh_active = False
        self._refresh_started = 0.0
        self._task_manager_copy_feedback_timer = QTimer(self)
        self._task_manager_copy_feedback_timer.setSingleShot(True)
        self._task_manager_copy_feedback_timer.timeout.connect(
            self._hide_task_manager_copy_feedback
        )
        self._settings_widget: SettingsDialog | None = None

        app_icon = build_app_icon()
        self.setWindowIcon(app_icon)
        application = QApplication.instance()
        if application is not None:
            application.setWindowIcon(app_icon)  # type: ignore[attr-defined]
        self.setWindowTitle("FREE · MuMu 自动化控制台")
        self.setFixedSize(1240, 820)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        self._build_ui()
        self._populate_tasks()
        self._report_config_errors(self.config_errors, popup=False)
        self._update_subtitle()
        self._update_device_status()

    def _build_ui(self) -> None:
        root_widget, content_splitter = self._build_main_page()
        self._build_settings_page()
        self._build_task_manager_page()

        self.pages = QStackedWidget()
        self.pages.setObjectName("appPages")
        self.main_page = root_widget
        self.pages.addWidget(self.main_page)
        self.pages.addWidget(self.settings_page)
        self.pages.addWidget(self.task_manager_page)
        self.setCentralWidget(self.pages)
        self.setStatusBar(QStatusBar())
        self.statusBar().hide()
        self.statusBar().showMessage("就绪")
        self._apply_style()
        splitter_handle = content_splitter.handle(1)
        if splitter_handle is not None:
            splitter_handle.setCursor(Qt.CursorShape.ArrowCursor)
            splitter_handle.setEnabled(False)

    def _build_main_page(self) -> tuple[QWidget, QSplitter]:
        root_widget = QWidget()
        root_widget.setObjectName("appRoot")
        root_layout = QVBoxLayout(root_widget)
        root_layout.setContentsMargins(0, 22, 0, 0)
        root_layout.setSpacing(14)
        root_layout.addLayout(self._build_header())

        task_panel = QFrame()
        task_panel.setObjectName("surface")
        # Match the task-manager page's 280px panel plus its 2px divider.
        task_panel.setFixedWidth(282)
        task_layout = QVBoxLayout(task_panel)
        task_layout.setContentsMargins(14, 14, 14, 14)
        task_layout.setSpacing(8)

        task_heading = QHBoxLayout()
        task_heading.setSpacing(8)
        task_title = QLabel("任务顺序")
        task_title.setObjectName("taskSectionTitle")
        task_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        task_hint = QLabel("拖拽排序")
        task_hint.setObjectName("taskSortHint")
        task_hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
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
        self.task_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.task_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.task_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.task_list.setDragEnabled(True)
        self.task_list.setAcceptDrops(True)
        self.task_list.currentRowChanged.connect(self._task_changed)
        self.task_list.order_changed.connect(self._save_task_order)
        task_layout.addWidget(self.task_list, 1)

        status_panel = QFrame()
        status_panel.setObjectName("statusPanel")
        status_layout = QVBoxLayout(status_panel)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(0)

        status_header = QHBoxLayout()
        status_header.setSpacing(10)
        self.status_label = QLabel("待命")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setProperty("state", "idle")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setMinimumWidth(78)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.run_task_label = QLabel("未选择任务")
        self.run_task_label.setObjectName("runTaskLabel")
        self.run_task_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.current_step_label = QLabel("选择任务后开始")
        self.current_step_label.setObjectName("currentStepLabel")
        self.current_step_label.setWordWrap(True)
        status_header.addWidget(self.status_label)
        status_header.addWidget(self.run_task_label)
        status_header.addWidget(self.current_step_label, 2)

        progress_heading = QHBoxLayout()
        progress_heading.setSpacing(18)
        self.overall_count_label = QLabel("全部任务 0 / 0")
        self.overall_count_label.setObjectName("progressLabel")
        self.step_count_label = QLabel("当前动作 0 / 0")
        self.step_count_label.setObjectName("progressLabel")
        progress_heading.addWidget(self.overall_count_label)
        progress_heading.addStretch(1)
        progress_heading.addWidget(self.step_count_label)

        self.overall_progress = QProgressBar()
        self.overall_progress.setObjectName("overallProgress")
        self.overall_progress.setRange(0, max(1, len(self.tasks)))
        self.overall_progress.setValue(0)
        self.overall_progress.setTextVisible(False)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("taskProgress")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setFont(QFont("Microsoft YaHei", 9))
        status_layout.addWidget(self.log_view, 1)

        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setObjectName("contentSplitter")
        content_splitter.addWidget(task_panel)
        content_splitter.addWidget(status_panel)
        content_splitter.setHandleWidth(0)
        content_splitter.setSizes([282, 800])
        content_splitter.setCollapsible(0, False)
        content_splitter.setStretchFactor(0, 0)
        content_splitter.setStretchFactor(1, 1)
        root_layout.addWidget(content_splitter, 1)
        return root_widget, content_splitter

    def _build_settings_page(self) -> None:
        self.settings_page = QWidget()
        self.settings_page.setObjectName("appRoot")
        settings_layout = QVBoxLayout(self.settings_page)
        settings_layout.setContentsMargins(28, 22, 28, 22)
        settings_layout.setSpacing(14)
        settings_header = QHBoxLayout()
        settings_header.setSpacing(10)
        settings_title = QLabel("设置")
        settings_title.setObjectName("settingsPageTitle")
        settings_header.addWidget(settings_title)
        settings_header.addStretch(1)
        self.settings_back_button = QPushButton("返回")
        self.settings_back_button.setObjectName("quietButton")
        self.settings_back_button.setFixedSize(74, 40)
        self.settings_back_button.clicked.connect(self._close_settings_page)
        settings_header.addWidget(self.settings_back_button)
        settings_layout.addLayout(settings_header)
        self.settings_page_layout = settings_layout

    def _build_task_manager_page(self) -> None:
        self.task_manager_page = QWidget()
        self.task_manager_page.setObjectName("appRoot")
        task_manager_layout = QVBoxLayout(self.task_manager_page)
        task_manager_layout.setContentsMargins(0, 0, 0, 0)
        task_manager_layout.setSpacing(0)
        task_manager_header = QHBoxLayout()
        task_manager_header.setSpacing(10)
        task_manager_header.setContentsMargins(28, 16, 28, 8)
        task_manager_title = QLabel("任务管理")
        task_manager_title.setObjectName("settingsPageTitle")
        task_manager_header.addWidget(task_manager_title)
        task_manager_header.addStretch(1)
        self.task_manager_pointer_button = QPushButton("显示坐标")
        self.task_manager_pointer_button.setObjectName("taskManagerPointerButton")
        self.task_manager_pointer_button.setCheckable(True)
        self.task_manager_pointer_button.setFixedSize(108, 40)
        self.task_manager_pointer_button.setToolTip("在 MuMu 模拟器中显示或隐藏鼠标坐标")
        self.task_manager_pointer_button.hide()
        task_manager_header.addWidget(self.task_manager_pointer_button)
        self.task_manager_copy_package_button = QPushButton("获取包名")
        self.task_manager_copy_package_button.setObjectName("secondaryButton")
        self.task_manager_copy_package_button.setFixedSize(108, 40)
        self.task_manager_copy_package_button.setToolTip("获取当前前台应用包名")
        self.task_manager_copy_package_button.hide()
        task_manager_header.addWidget(self.task_manager_copy_package_button)
        self.task_manager_dump_tree_button = QPushButton("抓取 UI 树")
        self.task_manager_dump_tree_button.setObjectName("secondaryButton")
        self.task_manager_dump_tree_button.setFixedSize(108, 40)
        self.task_manager_dump_tree_button.hide()
        task_manager_header.addWidget(self.task_manager_dump_tree_button)
        self.task_manager_view_json_button = QPushButton("查看 JSON")
        self.task_manager_view_json_button.setObjectName("secondaryButton")
        self.task_manager_view_json_button.setFixedSize(108, 40)
        self.task_manager_view_json_button.hide()
        task_manager_header.addWidget(self.task_manager_view_json_button)
        self.task_manager_back_button = QPushButton("返回")
        self.task_manager_back_button.setObjectName("quietButton")
        self.task_manager_back_button.setFixedSize(74, 40)
        self.task_manager_back_button.clicked.connect(self._task_manager_back)
        task_manager_header.addWidget(self.task_manager_back_button)
        task_manager_layout.addLayout(task_manager_header)
        self.task_manager_page_layout = task_manager_layout
        self._task_manager_widget: TaskManagerWidget | None = None

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
        # Fixed total width so the leading stretch absorbs the "刷新" to
        # "刷新中…" growth without squeezing the buttons.
        action_bar.setFixedSize(640, 50)
        layout.addWidget(action_bar)
        return layout

    def _build_action_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.task_manager_button = QPushButton("任务管理")
        self.task_manager_button.setObjectName("secondaryButton")
        self.task_manager_button.setFixedSize(108, 48)
        self.task_manager_button.clicked.connect(self._open_task_manager)

        self.run_all_button = QPushButton("执行全部")
        self.run_all_button.setObjectName("secondaryButton")
        self.run_all_button.setFixedSize(176, 48)
        self.run_all_button.setProperty("runState", "idle")
        self.run_all_button.clicked.connect(self._start_all_tasks)

        self.start_button = QPushButton("执行")
        self.start_button.setObjectName("secondaryButton")
        self.start_button.setFixedSize(176, 48)
        self.start_button.setProperty("runState", "idle")
        self.start_button.clicked.connect(self._start_current_task)

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("secondaryButton")
        # Minimum (not fixed) width so the button can grow to show "刷新中…"
        # without clipping its text; the header's leading stretch absorbs it.
        self.refresh_button.setMinimumSize(74, 48)
        self.refresh_button.clicked.connect(self._refresh_all)

        self.settings_button = QToolButton()
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setText("⚙")
        # The gear is an icon glyph, not interface text; keep its symbol font
        # so it renders as a compact monochrome icon instead of a fallback glyph.
        self.settings_button.setFont(QFont("Segoe UI Symbol", 15))
        self.settings_button.setFixedSize(48, 48)
        self.settings_button.clicked.connect(self._open_settings)

        layout.addStretch(1)
        layout.addWidget(self.task_manager_button)
        layout.addWidget(self.run_all_button)
        layout.addWidget(self.start_button)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.settings_button)
        return layout

    def _update_subtitle(self) -> None:
        vmindex = self.settings.get("mumu_vmindex", 0)
        self.subtitle_label.setText(
            f"实例 {vmindex}  ·  {SCREEN_WIDTH}×{SCREEN_HEIGHT}  ·  {SCREEN_DENSITY} dpi"
        )

    def _effective_screenshot_mode(self) -> str:
        try:
            max_files = int(self.settings.get("screenshot_max_files", -1))
        except (TypeError, ValueError):
            max_files = -1
        # 截图只按"成功与失败（key）"级别保存；screenshot_max_files=0 时完全不保存。
        return "none" if max_files == 0 else "key"

    def _set_execution_controls(self, state: str) -> None:
        if state in ("running", "stopping"):
            (
                active_text,
                active_enabled,
                inactive_text,
                inactive_enabled,
                active_state,
                inactive_state,
            ) = (
                {
                    "running": ("停止", True, "执行", False, "stop", "busy"),
                    "stopping": ("正在停止…", False, "执行", False, "stopping", "busy"),
                }[state]
            )
            active = self.run_all_button if self.run_mode == "batch" else self.start_button
            inactive = self.start_button if self.run_mode == "batch" else self.run_all_button
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
            active_state if self.run_mode == "batch" and state != "idle" else inactive_state,
        )
        self.start_button.setProperty(
            "runState",
            active_state if self.run_mode == "single" and state != "idle" else inactive_state,
        )
        for button in (self.run_all_button, self.start_button):
            self._refresh_style(button)

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
            QLabel#sectionTitle {
                color: #203637;
                font-size: 14px;
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
            QLabel#settingsPageTitle {
                color: #203637;
                font-size: 22px;
                font-weight: 750;
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
                min-width: 46px;
                max-width: 46px;
                min-height: 38px;
                max-height: 38px;
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
            QLabel#runTaskLabel {
                color: #175e59;
                font-weight: 700;
            }
            QLabel#currentStepLabel {
                color: #56696a;
                background: #f5f8f7;
                border: 1px solid #e0e9e6;
                border-radius: 5px;
                padding: 7px 10px;
            }
            QLabel#progressLabel {
                color: #526667;
                font-size: 11px;
                font-weight: 600;
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
            QProgressBar#overallProgress, QProgressBar#taskProgress {
                background: #e7efec;
                border: 0;
                border-radius: 3px;
                min-height: 7px;
                max-height: 7px;
            }
            QProgressBar#overallProgress::chunk {
                background: #e5a848;
                border-radius: 3px;
            }
            QProgressBar#taskProgress::chunk {
                background: #159487;
                border-radius: 3px;
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
            """ + _s.MESSAGE_BOX_QSS + _s.SCROLLBAR_QSS
            + _s.COMMON_CONTROLS_QSS
        )

    def _populate_tasks(self) -> None:
        self.task_list.clear()
        self.task_meta_label.setText(f"{len(self.tasks)} 个任务")
        for task in self.tasks:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, task.id)
            item.setSizeHint(QSize(0, 70))
            self.task_list.addItem(item)
            self._update_task_item(task.id)
        if self.tasks:
            self.task_list.setCurrentRow(0)

    def _display_task_name(self, task: TaskDefinition) -> str:
        return self._UI_TASK_NAMES.get(task.id, task.name)

    def _task_from_item(self, item: QListWidgetItem | None) -> TaskDefinition | None:
        if item is None:
            return None
        return self.task_by_id.get(str(item.data(Qt.ItemDataRole.UserRole)))

    def _tasks_in_list(self) -> list[TaskDefinition]:
        ordered: list[TaskDefinition] = []
        for row in range(self.task_list.count()):
            task = self._task_from_item(self.task_list.item(row))
            if task is not None:
                ordered.append(task)
        return ordered

    def _update_task_item(self, task_id: str) -> None:
        for row in range(self.task_list.count()):
            item = self.task_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) != task_id:
                continue
            task = self.task_by_id[task_id]
            state = self.task_states.get(task_id, "pending")
            item.setText(
                f"{row + 1:02d}  {self._display_task_name(task)}\n"
                f"{self._STATE_LABELS.get(state, state)}  ·  {len(task.actions)} 个动作"
            )
            item.setForeground(self._STATE_COLORS.get(state, self._STATE_COLORS["pending"]))
            return

    def _update_all_task_items(self) -> None:
        for task in self._tasks_in_list():
            self._update_task_item(task.id)

    def _select_task_in_list(self, task_id: str) -> None:
        for row in range(self.task_list.count()):
            item = self.task_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == task_id:
                self.task_list.setCurrentRow(row)
                self.task_list.scrollToItem(item)
                return

    def _selected_task(self) -> TaskDefinition | None:
        return self._task_from_item(self.task_list.currentItem())

    @Slot(int)
    def _task_changed(self, _row: int) -> None:
        task = self._selected_task()
        if task and not self.worker_thread:
            display_name = self._display_task_name(task)
            self.run_task_label.setText(display_name)
            self.step_count_label.setText(f"当前动作 0 / {len(task.actions)}")
            self.progress_bar.setRange(0, max(1, len(task.actions)))
            self.progress_bar.setValue(0)
            self.current_step_label.setText(f"{task.id} · 等待开始")
            self._set_status("待命", "idle")

    def _reload_tasks(self) -> bool:
        """Rescan the task directory and rebuild task state.

        Returns False when the directory cannot be loaded.
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

    def _report_config_errors(self, errors: list[TaskFileError], *, popup: bool) -> None:
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
        """Reload tasks, rebuild the list, and report any config errors.

        Returns False when the task directory cannot be loaded; in that case the
        task state is left untouched and callers keep their own failure handling.
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
            self._save_main_setting("task_order", task_order)
            self.settings["task_order"] = task_order
            self._update_all_task_items()
            self.statusBar().showMessage("任务顺序已自动保存")
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", f"无法保存任务顺序：{exc}")

    def _save_main_setting(self, key: str, value: object) -> None:
        persisted = load_json(self.settings_path) if self.settings_path.exists() else {}
        if not isinstance(persisted, dict):
            raise ValueError(f"配置文件必须是对象: {self.settings_path}")
        persisted[key] = value
        save_settings(self.settings_path, persisted)

    def _make_adb(self) -> AdbClient:
        configured = self.settings.get("adb_path")
        candidates: list[Path] = []
        if configured:
            candidates.append(Path(configured))
        folder_value = self.settings.get("mumu_directory")
        if isinstance(folder_value, str) and folder_value.strip():
            folder = Path(folder_value.strip())
            candidates.extend(
                [
                    folder / "nx_main" / "adb.exe",
                    folder / "shell" / "adb.exe",
                    folder / "adb.exe",
                ]
            )
        candidates.extend(
            [
                Path(r"D:\APP\MuMu Player 12\nx_main\adb.exe"),
                Path(r"D:\APP\MuMu Player 12\shell\adb.exe"),
            ]
        )
        executable = next((path for path in candidates if path.exists()), candidates[0])
        return AdbClient(
            executable=executable,
            command_timeout=float(self.settings.get("command_timeout_seconds", 15)),
        )

    def _mumu_forwarded_adb_address(self) -> str | None:
        return mumu_adb_address_from_settings(self.settings)

    @Slot()
    def _refresh_device(self) -> None:
        if self._refresh_active:
            return
        self._refresh_active = True
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("刷新中…")
        self._refresh_started = time.monotonic()
        self._update_device_status(finalize_refresh=True)

    def _finalize_refresh_button(self) -> None:
        remaining = _REFRESH_MIN_DISPLAY_SEC - (time.monotonic() - self._refresh_started)
        delay_ms = max(0, int(remaining * 1000))
        QTimer.singleShot(delay_ms, self._restore_refresh_button)

    def _restore_refresh_button(self) -> None:
        self._refresh_active = False
        self.refresh_button.setText("刷新")
        self.refresh_button.setEnabled(True)

    def _update_device_status(self, finalize_refresh: bool = False) -> None:
        self.device_label.setText("设备：检查中…")
        self.device_label.setProperty("state", "checking")
        self._refresh_style(self.device_label)
        try:
            adb = self._make_adb()
            forwarded_address = None
            try:
                forwarded_address = self._mumu_forwarded_adb_address()
            except (MuMuError, OSError, ValueError, TypeError):
                forwarded_address = None
            if forwarded_address:
                try:
                    adb.connect(forwarded_address)
                except Exception:
                    pass
            if not forwarded_address:
                raise MuMuError("MuMu 未返回动态 ADB 地址")
            devices = adb.list_devices()
            selected = next(
                (device for device in devices if device.serial == forwarded_address),
                None,
            )
            if selected and selected.state == "device":
                self.device_label.setText(f"MuMu · {selected.serial} · 可用")
                self.device_label.setProperty("state", "ready")
                self.statusBar().showMessage("MuMu ADB 设备可用")
            elif selected:
                self.device_label.setText(f"MuMu · {selected.serial} · {selected.state}")
                self.device_label.setProperty("state", "error")
                self.statusBar().showMessage("MuMu 设备当前不可用")
            else:
                self.device_label.setText("MuMu · 未找到设备")
                self.device_label.setProperty("state", "error")
                self.statusBar().showMessage("未找到可用 MuMu ADB 设备")
        except Exception as exc:
            self.device_label.setText("MuMu · ADB 不可用")
            self.device_label.setProperty("state", "error")
            self.statusBar().showMessage(str(exc))
        finally:
            self._refresh_style(self.device_label)
            if finalize_refresh:
                self._finalize_refresh_button()

    @Slot(str)
    def _append_log(self, message: str) -> None:
        line = format_log_line(message)
        self.log_view.appendPlainText(line)
        scroll_bar = self.log_view.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())
        if self.log_file:
            self.log_file.write(line + "\n")
            self.log_file.flush()

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
        log_directory = resolve_path(self.settings.get("log_directory"), self.base_directory)
        screenshot_directory = resolve_path(
            self.settings.get("screenshot_directory"), self.base_directory
        )
        log_max_files = int(self.settings.get("log_max_files", -1))
        log_path: Path | None = None
        if debug_mode:
            log_path = None
            self.log_file = None
        elif log_max_files != 0:
            log_directory.mkdir(parents=True, exist_ok=True)
            log_path = log_directory / f"run_{datetime.now():%Y%m%d_%H%M%S}.log"
            self.log_file = log_path.open("w", encoding="utf-8")
        else:
            self.log_file = None
        if not debug_mode:
            self.log_view.clear()
        self.active_tasks = tasks
        self.task_results = {}
        self.task_executions_done = 0
        self.task_states = {task.id: "pending" for task in self.tasks}
        self._update_all_task_items()
        self.overall_progress.setRange(0, max(1, len(tasks)))
        self.overall_progress.setValue(0)
        self.overall_count_label.setText(f"全部任务 0 / {len(tasks)}")
        self.progress_bar.setRange(0, max(1, len(tasks[0].actions)))
        self.progress_bar.setValue(0)
        append_log(
            f"开始执行 {len(tasks)} 个任务：{' → '.join(self._display_task_name(task) for task in tasks)}"
        )
        if log_path is not None:
            append_log(f"日志文件: {log_path}")
        poll_interval = float(self.settings.get("poll_interval_seconds", 0.5))
        if debug_mode:
            append_log("调试模式：直接连接已运行实例，不启动 MuMu、不清理 App")
        else:
            append_log(
                "运行配置: "
                f"poll_interval={poll_interval:g}s, "
                f"auto_start_mumu={bool(self.settings.get('auto_start_mumu', False))}, "
                f"close_mumu_after_run={bool(self.settings.get('close_mumu_after_run', False))}, "
                f"close_mumu_app_after_run="
                f"{bool(self.settings.get('close_mumu_app_after_run', False))}, "
                f"task_execution_counts={self.settings.get('task_execution_counts', {})}, "
                f"cleanup_after_task={bool(self.settings.get('cleanup_after_task', True))}"
            )

        self.worker_thread = QThread(self)
        if self.run_mode == "batch":
            batch_worker = BatchTaskWorker(
                tasks,
                adb=adb,
                screenshot_directory=screenshot_directory,
                poll_interval=poll_interval,
                screenshot_mode=self._effective_screenshot_mode(),
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
                poll_interval=poll_interval,
                screenshot_mode=self._effective_screenshot_mode(),
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

        self.task_list.setEnabled(False)
        self._set_execution_controls("running")
        self.refresh_button.setEnabled(False)
        self.device_label.setText("MuMu · 正在启动/连接")
        self.device_label.setProperty("state", "checking")
        self._refresh_style(self.device_label)
        self._set_status("运行中", "running")
        self.current_step_label.setText("准备连接 MuMu 设备…")
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
            task for task in all_tasks
            if task_execution_count(self.settings, task_id=task.id) == 0
        ]
        if not tasks:
            QMessageBox.warning(self, "没有任务", "全部任务的执行次数均为 0，没有可执行任务。")
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
            self._set_status("正在停止", "stopped")
            self.current_step_label.setText("等待当前 ADB 动作结束，随后关闭 App 进程…")
            self._set_execution_controls("stopping")

    @Slot(int, int, str)
    def _single_progress(self, index: int, total: int, description: str) -> None:
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(max(0, index - 1))
        self.step_count_label.setText(f"当前动作 {index} / {total}")
        self.current_step_label.setText(f"第 {index} 步 · {description}")

    @Slot(str, int, int)
    def _batch_task_started(self, task_id: str, index: int, total: int) -> None:
        self.task_states[task_id] = "running"
        self._update_task_item(task_id)
        self._select_task_in_list(task_id)
        task = self.task_by_id[task_id]
        self.run_task_label.setText(self._display_task_name(task))
        self.overall_count_label.setText(f"全部任务 {index - 1} / {total}")
        self.overall_progress.setValue(index - 1)
        self.progress_bar.setRange(0, max(1, len(task.actions)))
        self.progress_bar.setValue(0)
        self.step_count_label.setText(f"当前动作 0 / {len(task.actions)}")
        self.current_step_label.setText(f"准备执行第 {index} 个任务 · {self._display_task_name(task)}")

    @Slot(str, int, int, str)
    def _batch_progress(self, task_id: str, index: int, total: int, description: str) -> None:
        self._single_progress(index, total, description)
        self.run_task_label.setText(self._display_task_name(self.task_by_id[task_id]))

    @Slot(object)
    def _batch_task_finished(self, result: RunResult) -> None:
        self.task_results[result.task_id] = result
        self.task_executions_done += 1
        self.task_states[result.task_id] = result.status.value
        self._update_task_item(result.task_id)
        self.overall_progress.setValue(self.task_executions_done)
        self.overall_count_label.setText(
            f"全部任务 {self.task_executions_done} / {len(self.active_tasks)}"
        )

    @Slot(object)
    def _single_finished(self, result: RunResult) -> None:
        self.task_results[result.task_id] = result
        self.task_states[result.task_id] = result.status.value
        self._update_task_item(result.task_id)
        self.overall_progress.setValue(1)
        self.overall_count_label.setText("全部任务 1 / 1")
        self.progress_bar.setValue(min(result.completed_steps, max(1, result.total_steps)))
        self.step_count_label.setText(f"当前动作 {result.completed_steps} / {result.total_steps}")
        self._show_run_status(result.status, result.error, result.failed_step)

    @Slot(object)
    def _batch_finished(self, result: BatchRunResult) -> None:
        self.overall_progress.setValue(min(result.completed_tasks, max(1, result.total_tasks)))
        self.overall_count_label.setText(f"全部任务 {result.completed_tasks} / {result.total_tasks}")
        for task in self.active_tasks:
            if task.id not in self.task_results and result.status == RunStatus.STOPPED:
                self.task_states[task.id] = "skipped"
                self._update_task_item(task.id)
        detail = f"失败任务：{result.failed_task}" if result.failed_task else result.error
        self._show_run_status(result.status, detail, result.failed_task)

    def _show_run_status(
        self, status: RunStatus, error: str | None, failed_step: str | None
    ) -> None:
        if status == RunStatus.SUCCESS:
            self._set_status("成功", "success")
            if self.run_mode == "debug":
                self.current_step_label.setText("任务完成 · 调试模式未关闭 App")
                self.statusBar().showMessage("任务完成，调试模式未关闭 App")
            else:
                self.current_step_label.setText("任务完成 · App 进程已进入统一清理")
                self.statusBar().showMessage("任务完成，App 进程已关闭")
        elif status == RunStatus.STOPPED:
            self._set_status("已停止", "stopped")
            if self.run_mode == "debug":
                self.current_step_label.setText("任务已停止 · 调试模式未清理 App")
                self.statusBar().showMessage("任务已停止，调试模式未清理 App")
            else:
                self.current_step_label.setText("任务已停止 · App 进程已进入统一清理")
                self.statusBar().showMessage("任务已停止，未启动后续任务")
        else:
            self._set_status("有失败", "failed")
            detail = failed_step or error or "任务失败"
            self.current_step_label.setText(f"失败步骤 · {detail}")
            self.statusBar().showMessage(error or "任务失败")

    @Slot()
    def _thread_finished(self) -> None:
        if self.log_file:
            self.log_file.close()
            self.log_file = None
        if self.worker_thread:
            self.worker_thread.deleteLater()
        self.worker_thread = None
        self.worker = None
        self.run_mode = None
        self.task_list.setEnabled(True)
        self._set_execution_controls("idle")
        self.refresh_button.setEnabled(True)
        self._update_device_status()

    def _set_status(self, text: str, state: str) -> None:
        self.status_label.setText(text)
        self.status_label.setProperty("state", state)
        self._refresh_style(self.status_label)

    @staticmethod
    def _refresh_style(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    @Slot()
    def _open_settings(self) -> None:
        if self.worker_thread:
            return
        if self._settings_widget is None:
            dialog = SettingsDialog(
                self.settings_path,
                self,
                base_directory=self.base_directory,
                embedded=True,
            )
            dialog.setWindowFlags(Qt.WindowType.Widget)
            self._settings_widget = dialog
            self.settings_page_layout.addWidget(dialog)
            dialog.log_message.connect(self._append_log)
            dialog.ocr_test_finished.connect(
                lambda: self.pages.setCurrentWidget(self.main_page)
            )
            dialog.accepted.connect(self._on_settings_saved)
            dialog.rejected.connect(self._on_settings_back)
        self._settings_widget.rescan_model_status()
        self.pages.setCurrentWidget(self.settings_page)
        self._settings_widget.show()

    @Slot()
    def _open_task_manager(self) -> None:
        if self.worker_thread:
            self.statusBar().showMessage("任务运行中，请先停止任务。")
            return
        if self._task_manager_widget is None:
            widget = TaskManagerWidget(self.settings_path, self.base_directory)
            self._task_manager_widget = widget
            self.task_manager_page_layout.addWidget(widget)
            widget.tasks_changed.connect(self._on_task_manager_changed)
            widget.feedback_requested.connect(self._show_task_manager_feedback)
            widget.run_action_requested.connect(self._run_single_action_from_manager)
            widget.run_stop_requested.connect(self._stop_task)
            self.task_manager_pointer_button.toggled.connect(
                self._on_task_manager_pointer_toggled
            )
            self.task_manager_copy_package_button.clicked.connect(widget._on_copy_package_clicked)
            self.task_manager_dump_tree_button.clicked.connect(widget._on_dump_tree_clicked)
            self.task_manager_view_json_button.clicked.connect(widget._on_view_json_clicked)
        self._task_manager_widget.reload()
        self.pages.setCurrentWidget(self.task_manager_page)
        self._task_manager_widget.show()
        self.task_manager_pointer_button.show()
        self.task_manager_copy_package_button.show()
        self.task_manager_dump_tree_button.show()
        self.task_manager_view_json_button.show()

    def _on_task_manager_pointer_toggled(self, enabled: bool) -> None:
        widget = self._task_manager_widget
        if widget is None or widget.set_pointer_location(enabled):
            return
        self.task_manager_pointer_button.blockSignals(True)
        self.task_manager_pointer_button.setChecked(not enabled)
        self.task_manager_pointer_button.blockSignals(False)

    def _close_task_manager_page(self) -> None:
        self.pages.setCurrentWidget(self.main_page)

    def _show_task_manager_feedback(self, message: str) -> None:
        position = self.task_manager_copy_package_button.mapToGlobal(
            QPoint(0, self.task_manager_copy_package_button.height() + 6)
        )
        QToolTip.showText(position, message, self.task_manager_copy_package_button)
        self._task_manager_copy_feedback_timer.start(3000)

    def _hide_task_manager_copy_feedback(self) -> None:
        QToolTip.hideText()

    def _task_manager_back(self) -> None:
        if self._task_manager_widget is not None and self._task_manager_widget.go_back():
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
        """Run a single action emitted from the task manager."""
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
        self.run_task_label.setText(task_name or "单动作测试")
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

    def _confirm_exit_with_unsaved_manager_changes(self) -> bool:
        """Ask before closing while the task manager still has unsaved edits."""
        manager = self._task_manager_widget
        if manager is None or not manager.has_unsaved_changes():
            return True
        message_box = QMessageBox(self)
        message_box.setWindowTitle("未保存的修改")
        message_box.setText("任务管理器中有未保存的修改，确定退出吗？")
        cancel_button = message_box.addButton("取消", QMessageBox.ButtonRole.AcceptRole)
        confirm_button = message_box.addButton("确认", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button.setObjectName("messageBoxAction")
        confirm_button.setObjectName("messageBoxAction")
        confirm_button.setDefault(True)
        message_box.setEscapeButton(cancel_button)
        message_box.setStyleSheet(_s.MESSAGE_BOX_QSS)
        message_box.exec()
        return message_box.clickedButton() == confirm_button

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker_thread:
            self._stop_task()
            self.worker_thread.quit()
            self.worker_thread.wait(3000)
        if not self._confirm_exit_with_unsaved_manager_changes():
            event.ignore()
            return
        if self.log_file:
            self.log_file.close()
        event.accept()


def create_window(base_directory: Path) -> MainWindow:
    return MainWindow(base_directory)
