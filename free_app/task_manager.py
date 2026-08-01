"""Task manager panel: edit tasks, compound action library, and preview JSON."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .action_editor_dialogs import (
    ActionEditorWidget,
    ActionListEditorWidget,
)
from .action_schema import COMPOUND_TYPE, describe_action, validate_action_params
from .adb import AdbClient
from .config import (
    expand_action_for_run,
    load_json,
    load_settings,
    load_task_directory,
    save_settings,
)
from .helpers import deep_copy
from .logging_utils import format_log_line
from .message_box import QMessageBox
from .models import Action, RunResult, RunStatus, TaskDefinition
from .mumu import connect_to_mumu
from .settings_dialog import SettingsComboBox, confirm_dialog
from .trash import TrashError, send_to_recycle_bin
from .ui_automation import UiSnapshot, text_matches_exact, text_matches_fuzzy

from . import styles as _s


def _ui_tree_search_matches(label: str, resource_id: str, needle: str, mode: str) -> bool:
    """Filter UI tree rows with exact or fuzzy (% / _ / substring) matching."""

    needle = needle.strip()
    if not needle:
        return True
    if mode == "exact":
        return text_matches_exact(label, needle) or resource_id.strip() == needle
    return text_matches_fuzzy(label, needle) or needle.lower() in resource_id.lower()


def _apply_ui_tree_filter(owner: QWidget) -> None:
    """Hide UI-tree rows that do not match the current search terms.

    Used by :class:`UiTreeDumpWidget`, which
    expose the same ``search_edit`` / ``search_mode_combo`` / ``_all_items``
    attributes.
    """

    needle = owner.search_edit.text().strip()
    mode = str(owner.search_mode_combo.currentData() or "fuzzy")
    for item in owner._all_items:
        matched = (
            not needle
            or _ui_tree_search_matches(item.text(0), item.text(1), needle, mode)
        )
        item.setHidden(not matched)


class _TaskToast(QFrame):
    """Small non-modal notification used for short-lived task-manager errors."""

    def __init__(self, parent: QWidget, title: str, message: str) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("taskToast")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setStyleSheet(
            """
            QFrame#taskToast {
                background: #fff7f6;
                border: 1px solid #e6aaa5;
                border-radius: 10px;
            }
            QLabel#taskToastTitle {
                color: #a3403b;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#taskToastMessage {
                color: #5b3b3a;
                font-size: 12px;
            }
            """
        )
        title_label = QLabel(title, self)
        title_label.setObjectName("taskToastTitle")
        message_label = QLabel(message, self)
        message_label.setObjectName("taskToastMessage")
        message_label.setWordWrap(True)
        message_label.setMaximumWidth(330)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)
        layout.addWidget(title_label)
        layout.addWidget(message_label)
        self.adjustSize()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#e6aaa5"), 1.0))
        painter.setBrush(QColor("#fff7f6"))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)

    def show_for_five_seconds(self) -> None:
        self._move_to_parent_corner()
        self.show()
        self.raise_()
        QTimer.singleShot(5000, self.close)

    def _move_to_parent_corner(self) -> None:
        parent = self.parentWidget()
        window = parent.window() if parent is not None else None
        if window is not None and window.isVisible():
            frame = window.frameGeometry()
            self.move(
                frame.center().x() - self.width() // 2,
                frame.center().y() - self.height() // 2,
            )
            return
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.move(
                available.center().x() - self.width() // 2,
                available.center().y() - self.height() // 2,
            )


class _UiTreeDumpWorker(QObject):
    """Run the complete UI-tree ADB workflow away from the GUI thread."""

    succeeded = Signal(object, object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, settings: dict[str, Any]) -> None:
        super().__init__()
        self._settings = dict(settings)

    def run(self) -> None:
        try:
            adb = connect_to_mumu(self._settings)
            xml = adb.dump_ui()
            snapshot = UiSnapshot.from_xml(xml)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(snapshot, adb)
        finally:
            self.finished.emit()


class JsonViewerWidget(QWidget):
    """Embeddable read-only viewer for a task or compound action JSON payload."""

    closed = Signal()

    def __init__(self, parent: QWidget | None, data: Any) -> None:
        super().__init__(parent)
        self.setStyleSheet(self._style_sheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setFont(QFont("Microsoft YaHei", 10))
        self.editor.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
        layout.addWidget(self.editor, 1)

    def load_data(self, data: Any) -> None:
        """Load new data into the viewer."""
        self.editor.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))

    @staticmethod
    def _style_sheet() -> str:
        return (
            "\n"
            "            QWidget { background: transparent; color: #193331; }\n"
            "            QPlainTextEdit {\n"
            "                background: #ffffff;\n"
            "                border: 1px solid #cbdcd6;\n"
            "                border-radius: 8px;\n"
            "                padding: 10px;\n"
            "                color: #244340;\n"
            "                selection-background-color: #cce8e0;\n"
            "                font-size: 13px;\n"
            "            }\n"
            + _s.CARD_TITLE_QSS
            + _s.OCR_FEEDBACK_QSS
            + _s.MESSAGE_BOX_QSS
            + _s.COMMON_CONTROLS_QSS
            + "        "
        )


class RunViewerWidget(QWidget):
    """Embeddable output panel for single-action debug runs."""

    closed = Signal()
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(self._style_sheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_label = QLabel("运行输出")
        title_label.setObjectName("settingsCardTitle")
        header.addWidget(title_label)
        self.status_label = QLabel("待命")
        self.status_label.setObjectName("runStatusLabel")
        header.addWidget(self.status_label)
        header.addStretch(1)
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("runStopButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        header.addWidget(self.stop_button)
        layout.addLayout(header)

        self.progress_label = QLabel("准备运行")
        self.progress_label.setObjectName("settingsOcrFeedback")
        layout.addWidget(self.progress_label)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setObjectName("runLogEdit")
        self.log_edit.setReadOnly(True)
        self.log_edit.setFont(QFont("Microsoft YaHei", 10))
        layout.addWidget(self.log_edit, 1)

        hint = QLabel("调试运行不会自动清理 App 或关闭 MuMu。")
        hint.setObjectName("settingsOcrFeedback")
        layout.addWidget(hint)

    def start_run(self, task_name: str) -> None:
        """Reset the panel and mark the run as active."""
        self.status_label.setText("运行中")
        self._paint_status("#0c6e63")
        self.progress_label.setText(f"{task_name or '单动作测试'} · 准备连接设备")
        self.log_edit.clear()
        self.stop_button.setEnabled(True)

    def append_log(self, message: str) -> None:
        """Append one timestamped log line and keep the view scrolled down."""
        self.log_edit.appendPlainText(format_log_line(message))
        scroll_bar = self.log_edit.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    def set_progress(self, index: int, total: int, description: str) -> None:
        """Update the current step description."""
        self.progress_label.setText(f"当前动作 {index} / {total} · {description}")

    def finish_run(self, result: RunResult) -> None:
        """Show the final status and result summary in the embedded panel."""
        self.stop_button.setEnabled(False)
        if result.status == RunStatus.SUCCESS:
            self.status_label.setText("成功")
            self._paint_status("#0c6e63")
        elif result.status == RunStatus.STOPPED:
            self.status_label.setText("已停止")
            self._paint_status("#95651b")
        else:
            self.status_label.setText("失败")
            self._paint_status("#a3403b")
        self.progress_label.setText(f"当前动作 {result.completed_steps} / {result.total_steps}")
        error = f"，错误: {result.error}" if result.error else ""
        self.append_log(
            f"运行结束: status={result.status.value}, "
            f"completed={result.completed_steps}/{result.total_steps}{error}"
        )

    def abort_run(self, message: str) -> None:
        """Mark the panel as not started after a preparation failure."""
        self.stop_button.setEnabled(False)
        self.status_label.setText("未运行")
        self._paint_status("#a3403b")
        self.progress_label.setText("设备准备失败")
        self.append_log(message)

    def _paint_status(self, color: str) -> None:
        self.status_label.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {color}; "
            "background: #ffffff; border: 1px solid #cbdcd6; "
            "border-radius: 6px; padding: 7px 14px;"
        )

    @staticmethod
    def _style_sheet() -> str:
        return (
            "\n"
            "            QWidget { background: transparent; color: #193331; }\n"
            "            QPlainTextEdit#runLogEdit {\n"
            "                background: #ffffff;\n"
            "                border: 1px solid #cbdcd6;\n"
            "                border-radius: 8px;\n"
            "                padding: 10px;\n"
            "                color: #244340;\n"
            "                selection-background-color: #cce8e0;\n"
            "                font-size: 13px;\n"
            "            }\n"
            + _s.CARD_TITLE_QSS
            + _s.OCR_FEEDBACK_QSS
            + "            QPushButton#runStopButton {\n"
            "                color: #ffffff;\n"
            "                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d6534d, stop:1 #bd403b);\n"
            "                border: none;\n"
            "                font-weight: 700;\n"
            "            }\n"
            "            QPushButton#runStopButton:disabled {\n"
            "                color: #9aa9a7;\n"
            "                background: #e9eeec;\n"
            "                border: 1px solid #dce5e2;\n"
            "            }\n"
            "            QPushButton#runStopButton:hover {\n"
            "                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #bd403b, stop:1 #a93632);\n"
            "            }\n"
            + _s.MESSAGE_BOX_QSS
            + _s.COMMON_CONTROLS_QSS
            + "        "
        )


class UiTreeDumpWidget(QWidget):
    """Embeddable UI tree dump widget for debugging; insert a click action from a node."""

    action_inserted = Signal(dict)
    try_click_requested = Signal(int, int, str)
    closed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(self._style_sheet())
        self._snapshot: UiSnapshot | None = None
        self._all_items: list[QTreeWidgetItem] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        title_label = QLabel("UI 树查看器")
        title_label.setObjectName("settingsCardTitle")
        layout.addWidget(title_label)
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索文本或 resource-id…")
        self.search_edit.textChanged.connect(self._apply_filter)
        self.search_edit.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        search_row.addWidget(self.search_edit, 1)
        self.search_mode_combo = SettingsComboBox()
        self.search_mode_combo.addItem("模糊", "fuzzy")
        self.search_mode_combo.addItem("精确", "exact")
        self.search_mode_combo.setCurrentIndex(self.search_mode_combo.findData("fuzzy"))
        self.search_mode_combo.currentIndexChanged.connect(self._apply_filter)
        self.search_mode_combo.setFixedWidth(88)
        search_row.addWidget(self.search_mode_combo)
        self.try_click_button = QPushButton("尝试点击")
        self.try_click_button.setObjectName("settingsTryButton")
        self.try_click_button.setToolTip("在模拟器中点击当前选中节点的中心")
        self.try_click_button.clicked.connect(self._try_click)
        self.try_click_button.setEnabled(False)
        search_row.addWidget(self.try_click_button)
        self.insert_mode_combo = SettingsComboBox()
        self.insert_mode_combo.addItem("ID", "resource_id")
        self.insert_mode_combo.addItem("文本", "text")
        search_row.addWidget(self.insert_mode_combo)
        self.insert_button = QPushButton("插入点击动作")
        self.insert_button.setObjectName("settingsTestButton")
        self.insert_button.clicked.connect(self._insert_click)
        search_row.addWidget(self.insert_button)
        layout.addLayout(search_row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["文本 / 描述", "resource-id", "坐标"])
        self.tree.setColumnWidth(0, 280)
        self.tree.setColumnWidth(1, 280)
        self.tree.itemDoubleClicked.connect(lambda _item, _column: self._insert_click())
        layout.addWidget(self.tree, 1)
        hint = QLabel("选中节点后可先尝试点击，或双击节点生成 click 动作插入任务动作列表。")
        hint.setObjectName("settingsOcrFeedback")
        layout.addWidget(hint)

    @staticmethod
    def _style_sheet() -> str:
        return (
            "\n"
            "            QWidget { background: transparent; color: #193331; }\n"
            "            QTreeWidget {\n"
            "                background: #ffffff;\n"
            "                border: 1px solid #cbdcd6;\n"
            "                border-radius: 8px;\n"
            "                color: #244340;\n"
            "                outline: 0;\n"
            "            }\n"
            "            QTreeWidget::item {\n"
            "                min-height: 28px;\n"
            "                padding: 4px 8px;\n"
            "                border: none;\n"
            "            }\n"
            "            QTreeWidget::item:selected {\n"
            "                background: #dff2ed;\n"
            "                color: #0f625b;\n"
            "            }\n"
            "            QTreeWidget::item:hover {\n"
            "                background: #edf7f4;\n"
            "            }\n"
            + _s.CARD_TITLE_QSS
            + _s.OCR_FEEDBACK_QSS
            + "            QPushButton#settingsTryButton {\n"
            "                color: #ffffff;\n"
            "                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2b9b8b, stop:1 #137f73);\n"
            "                border: none;\n"
            "                font-weight: 700;\n"
            "            }\n"
            "            QPushButton#settingsTryButton:hover {\n"
            "                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a9385, stop:1 #0e6e64);\n"
            "            }\n"
            + _s.MESSAGE_BOX_QSS
            + _s.COMMON_CONTROLS_QSS
            + "        "
        )

    def load_snapshot(self, snapshot: UiSnapshot) -> None:
        """Load UI snapshot data into the tree."""
        self._snapshot = snapshot
        self.tree.clear()
        self._all_items.clear()
        self.search_edit.clear()
        for index, node in enumerate(snapshot.nodes):
            if not node.clickable:
                continue
            label = node.label or ""
            resource_id = node.resource_id or ""
            bounds = (
                f"[{node.bounds.left},{node.bounds.top}][{node.bounds.right},{node.bounds.bottom}]"
                if node.bounds
                else ""
            )
            item = QTreeWidgetItem([label, resource_id, bounds])
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            self.tree.addTopLevelItem(item)
        self._all_items = []
        for index in range(self.tree.topLevelItemCount()):
            tree_item = self.tree.topLevelItem(index)
            if tree_item is not None:
                self._all_items.append(tree_item)
        self.try_click_button.setEnabled(bool(self._all_items))

    def _apply_filter(self, _text: str) -> None:
        _apply_ui_tree_filter(self)

    def _insert_click(self) -> None:
        item = self.tree.currentItem()
        if item is None or self._snapshot is None:
            return
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(index, int) or index < 0 or index >= len(self._snapshot.nodes):
            return
        node = self._snapshot.nodes[index]
        mode = self.insert_mode_combo.currentData() or "text"
        if mode == "resource_id":
            if not node.resource_id:
                QMessageBox.information(self, "无法插入", "该节点没有 resource-id，请选择其他插入方式。")
                return
            action = {
                "type": "click",
                "locate": "ui",
                "target": "resource_id",
                "resource_id": node.resource_id,
                "timeout_seconds": 15,
            }
        elif mode == "text":
            if not node.label:
                QMessageBox.information(self, "无法插入", "该节点没有文本，请选择其他插入方式。")
                return
            action = {
                "type": "click",
                "locate": "ui",
                "target": "text",
                "text": node.label,
                "match_mode": "exact",
                "timeout_seconds": 15,
            }
        else:
            QMessageBox.information(self, "无法插入", "请选择 ID 或文本插入方式。")
            return
        self.action_inserted.emit(action)

    def _try_click(self) -> None:
        if self._snapshot is None:
            QMessageBox.information(self, "无法尝试点击", "请先抓取 UI 树。")
            return
        item = self.tree.currentItem()
        if item is None:
            QMessageBox.information(self, "无法尝试点击", "请先选择一个节点。")
            return
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(index, int) or index < 0 or index >= len(self._snapshot.nodes):
            return
        node = self._snapshot.nodes[index]
        if not node.bounds:
            QMessageBox.information(self, "无法尝试点击", "该节点没有可点击坐标。")
            return
        x, y = node.bounds.center
        self.try_click_requested.emit(x, y, node.label)


class TaskManagerWidget(QWidget):
    """Full task management panel: tasks, action editor, compound library."""

    # Maximum nesting of embedded step/action editors that _commit_embedded_editors
    # must unwind (task -> if branch step -> action -> nested steps).
    _MAX_EMBEDDED_EDITOR_DEPTH = 4

    tasks_changed = Signal()
    feedback_requested = Signal(str)
    run_action_requested = Signal(list, str, str)  # actions, package, task_name
    run_stop_requested = Signal()

    def __init__(self, settings_path: Path, base_directory: Path | None = None):
        super().__init__()
        self.settings_path = settings_path
        self.base_directory = base_directory or settings_path.parent
        self.settings = load_settings(settings_path)
        self._ui_tree_adb: AdbClient | None = None
        self._ui_tree_thread: QThread | None = None
        self._ui_tree_worker: _UiTreeDumpWorker | None = None
        self._tasks: dict[str, dict[str, Any]] = {}
        self._compound_library: dict[str, dict[str, Any]] = {}
        self._selected_task: str | None = None
        self._selected_compound: str | None = None
        self._creating_task = False
        self._creating_compound = False
        self._actions_buffer: list[dict[str, Any]] = []
        self._params_buffer: list[str] = []
        self._steps_buffer: list[dict[str, Any]] = []
        self._compound_description_buffer = ""
        self._compound_description_present = False
        self._embedded_editor_mode: str | None = None  # task_action, compound_step, branch_step
        self._embedded_editor_index: int = -1
        self._embedded_editor_return_panel: QWidget | None = None
        self._embedded_previous_panel: QWidget | None = None
        self._task_toast: _TaskToast | None = None
        self._branch_steps_stack: list[dict[str, Any]] = []
        self._dirty = False
        self._suspend_dirty = False
        self._embedded_editor_original: dict[str, Any] | None = None
        self._unsaved_task_ids: set[str] = set()
        self._unsaved_compound_names: set[str] = set()
        self._active_tab_index = 0
        self._build_ui()
        self.setStyleSheet(self._style_sheet())
        self.reload()
    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        # Keep the editor body flush with the page edges, matching the main
        # page surface.  The header owns its own horizontal margins.
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(10)
        header.addStretch(1)
        self.copy_package_button = QPushButton("获取包名")
        self.copy_package_button.setObjectName("settingsTestButton")
        self.copy_package_button.clicked.connect(self._on_copy_package_clicked)
        self.copy_package_button.hide()
        header.addWidget(self.copy_package_button)
        self.dump_tree_button = QPushButton("抓取 UI 树")
        self.dump_tree_button.setObjectName("settingsTestButton")
        self.dump_tree_button.clicked.connect(self._on_dump_tree_clicked)
        self.dump_tree_button.hide()
        header.addWidget(self.dump_tree_button)
        self.view_json_button = QPushButton("查看 JSON")
        self.view_json_button.setObjectName("settingsTestButton")
        self.view_json_button.clicked.connect(self._on_view_json_clicked)
        self.view_json_button.hide()
        header.addWidget(self.view_json_button)
        root.addLayout(header)

        h_line = QFrame()
        h_line.setObjectName("taskManagerHLine")
        h_line.setFixedHeight(2)
        root.addWidget(h_line)

        body = QHBoxLayout()
        body.setSpacing(0)

        # ---- left: tabbed lists (tasks / compound actions)
        left_panel = QFrame()
        left_panel.setObjectName("taskManagerLeftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(8)

        self.left_tabs = QTabWidget()
        self.left_tabs.setObjectName("taskManagerTabs")
        self.left_tabs.tabBar().setExpanding(True)

        task_tab = QWidget()
        task_tab_layout = QVBoxLayout(task_tab)
        task_tab_layout.setContentsMargins(0, 8, 0, 0)
        task_tab_layout.setSpacing(8)
        self.task_list = QListWidget()
        self.task_list.setObjectName("settingsTaskList")
        self.task_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.task_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.task_list.currentRowChanged.connect(self._on_task_selected)
        self.task_list.itemClicked.connect(self._on_task_item_clicked)
        task_tab_layout.addWidget(self.task_list, 1)
        self.left_tabs.addTab(task_tab, "任务")

        compound_tab = QWidget()
        compound_tab_layout = QVBoxLayout(compound_tab)
        compound_tab_layout.setContentsMargins(0, 8, 0, 0)
        compound_tab_layout.setSpacing(8)
        self.compound_list = QListWidget()
        self.compound_list.setObjectName("settingsTaskList")
        self.compound_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.compound_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.compound_list.currentRowChanged.connect(self._on_compound_selected)
        self.compound_list.itemClicked.connect(self._on_compound_item_clicked)
        self.compound_list.itemDoubleClicked.connect(lambda _item: self._edit_compound())
        compound_tab_layout.addWidget(self.compound_list, 1)
        self.left_tabs.addTab(compound_tab, "复合任务")

        self.left_tabs.currentChanged.connect(self._on_editor_tab_changed)
        left_layout.addWidget(self.left_tabs, 1)

        # Hidden combo carrying the cleanup mode used by delete/save paths.
        self.cleanup_mode_combo = SettingsComboBox()
        self.cleanup_mode_combo.addItem("删除至回收站", "recycle")
        self.cleanup_mode_combo.addItem("永久删除", "permanent")
        self.cleanup_mode_combo.currentIndexChanged.connect(self._on_cleanup_mode_changed)
        self.cleanup_mode_combo.hide()

        left_panel.setFixedWidth(280)
        body.addWidget(left_panel)

        v_line = QFrame()
        v_line.setObjectName("taskManagerVLine")
        v_line.setFixedWidth(2)
        body.addWidget(v_line)

        # ---- right: stacked editors (tasks / compound actions)
        self.editor_stack = QStackedWidget()
        self.editor_stack.setObjectName("taskManagerEditors")

        # -- task editor
        editor_panel = QFrame()
        editor_panel.setObjectName("taskManagerEditorPanel")
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(14, 14, 14, 14)
        editor_layout.setSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        self.task_id_edit = QLineEdit()
        self.task_id_edit.setPlaceholderText("唯一标识，如 bilibili_exp")
        self._disable_context_menu(self.task_id_edit)
        self.task_name_edit = QLineEdit()
        self.task_name_edit.setPlaceholderText("任务显示名称")
        self._disable_context_menu(self.task_name_edit)
        self.task_package_edit = QLineEdit()
        self.task_package_edit.setPlaceholderText("应用包名，如 tv.danmaku.bili")
        self._disable_context_menu(self.task_package_edit)
        form.addRow("id", self.task_id_edit)
        form.addRow("名称", self.task_name_edit)
        form.addRow("包名", self.task_package_edit)
        for field in (self.task_id_edit, self.task_name_edit, self.task_package_edit):
            field.textChanged.connect(self._mark_dirty)
        editor_layout.addLayout(form)

        actions_header = QHBoxLayout()
        actions_header.setSpacing(8)
        actions_header.addWidget(self._card_title("动作列表"))
        actions_header.addStretch(1)
        editor_layout.addLayout(actions_header)

        self.actions_list = QListWidget()
        self.actions_list.setObjectName("settingsTaskList")
        self.actions_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.actions_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.actions_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.actions_list.model().rowsMoved.connect(self._on_actions_rows_moved)
        self.actions_list.currentRowChanged.connect(self._on_action_selection_changed)
        self.actions_list.itemDoubleClicked.connect(lambda _item: self._edit_action())
        editor_layout.addWidget(self.actions_list, 1)
        self.editor_stack.addWidget(editor_panel)

        # -- compound editor
        compound_editor_panel = QFrame()
        compound_editor_panel.setObjectName("taskManagerEditorPanel")
        compound_editor_layout = QVBoxLayout(compound_editor_panel)
        compound_editor_layout.setContentsMargins(14, 14, 14, 14)
        compound_editor_layout.setSpacing(12)

        compound_form = QFormLayout()
        compound_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        compound_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        compound_form.setHorizontalSpacing(14)
        compound_form.setVerticalSpacing(10)
        self.compound_name_edit = QLineEdit()
        self.compound_name_edit.setPlaceholderText("唯一标识，如 share_group")
        self._disable_context_menu(self.compound_name_edit)
        self.compound_name_edit.textChanged.connect(self._mark_dirty)
        compound_form.addRow("名称", self.compound_name_edit)
        compound_editor_layout.addLayout(compound_form)

        steps_header = QHBoxLayout()
        steps_header.setSpacing(8)
        steps_header.addWidget(self._card_title("步骤列表"))
        steps_header.addStretch(1)
        compound_editor_layout.addLayout(steps_header)

        self.compound_steps_list = QListWidget()
        self.compound_steps_list.setObjectName("settingsTaskList")
        self.compound_steps_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.compound_steps_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.compound_steps_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.compound_steps_list.model().rowsMoved.connect(self._on_compound_steps_moved)
        self.compound_steps_list.currentRowChanged.connect(self._on_step_selection_changed)
        self.compound_steps_list.itemDoubleClicked.connect(lambda _item: self._edit_step())
        compound_editor_layout.addWidget(self.compound_steps_list, 1)
        self.editor_stack.addWidget(compound_editor_panel)

        # -- embedded action editor
        self.embedded_action_editor_panel = QFrame()
        self.embedded_action_editor_panel.setObjectName("taskManagerEditorPanel")
        embedded_layout = QVBoxLayout(self.embedded_action_editor_panel)
        embedded_layout.setContentsMargins(0, 0, 0, 0)
        embedded_layout.setSpacing(0)
        self.embedded_action_editor = ActionEditorWidget(
            self.embedded_action_editor_panel,
            initial={},
            compound_library=self._compound_library,
            allow_compound=True,
        )
        self.embedded_action_editor.changed.connect(self._mark_dirty)
        self.embedded_action_editor.saved.connect(self._on_embedded_editor_saved)
        self.embedded_action_editor.cancelled.connect(self._on_embedded_editor_cancelled)
        self.embedded_action_editor.nested_steps_edit_requested.connect(
            self._open_branch_steps_editor
        )
        embedded_layout.addWidget(self.embedded_action_editor)
        self.editor_stack.addWidget(self.embedded_action_editor_panel)

        # -- embedded branch steps editor
        self.embedded_branch_steps_editor_panel = QFrame()
        self.embedded_branch_steps_editor_panel.setObjectName("taskManagerEditorPanel")
        branch_steps_layout = QVBoxLayout(self.embedded_branch_steps_editor_panel)
        branch_steps_layout.setContentsMargins(14, 14, 14, 14)
        branch_steps_layout.setSpacing(12)
        self.embedded_branch_steps_editor = ActionListEditorWidget(
            self.embedded_branch_steps_editor_panel
        )
        self.embedded_branch_steps_editor.saved.connect(self._on_branch_steps_saved)
        self.embedded_branch_steps_editor.cancelled.connect(self._on_branch_steps_cancelled)
        self.embedded_branch_steps_editor.add_step_requested.connect(self._add_branch_step)
        self.embedded_branch_steps_editor.edit_step_requested.connect(self._edit_branch_step)
        branch_steps_layout.addWidget(self.embedded_branch_steps_editor)
        self.editor_stack.addWidget(self.embedded_branch_steps_editor_panel)

        # -- embedded JSON viewer
        self.embedded_json_viewer_panel = QFrame()
        self.embedded_json_viewer_panel.setObjectName("taskManagerEditorPanel")
        json_viewer_layout = QVBoxLayout(self.embedded_json_viewer_panel)
        json_viewer_layout.setContentsMargins(0, 0, 0, 0)
        json_viewer_layout.setSpacing(0)
        self.embedded_json_viewer = JsonViewerWidget(self.embedded_json_viewer_panel, data={})
        self.embedded_json_viewer.closed.connect(self._close_embedded_viewer)
        json_viewer_layout.addWidget(self.embedded_json_viewer)
        self.editor_stack.addWidget(self.embedded_json_viewer_panel)

        # -- embedded UI tree viewer
        self.embedded_ui_tree_viewer_panel = QFrame()
        self.embedded_ui_tree_viewer_panel.setObjectName("taskManagerEditorPanel")
        ui_tree_viewer_layout = QVBoxLayout(self.embedded_ui_tree_viewer_panel)
        ui_tree_viewer_layout.setContentsMargins(0, 0, 0, 0)
        ui_tree_viewer_layout.setSpacing(0)
        self.embedded_ui_tree_viewer = UiTreeDumpWidget(self.embedded_ui_tree_viewer_panel)
        self.embedded_ui_tree_viewer.closed.connect(self._close_embedded_viewer)
        self.embedded_ui_tree_viewer.action_inserted.connect(
            self._on_embedded_ui_tree_action_inserted
        )
        self.embedded_ui_tree_viewer.try_click_requested.connect(
            self._on_embedded_ui_tree_try_click_requested
        )
        ui_tree_viewer_layout.addWidget(self.embedded_ui_tree_viewer)
        self.editor_stack.addWidget(self.embedded_ui_tree_viewer_panel)

        # -- embedded run viewer
        self.embedded_run_viewer_panel = QFrame()
        self.embedded_run_viewer_panel.setObjectName("taskManagerEditorPanel")
        run_viewer_layout = QVBoxLayout(self.embedded_run_viewer_panel)
        run_viewer_layout.setContentsMargins(0, 0, 0, 0)
        run_viewer_layout.setSpacing(0)
        self.embedded_run_viewer = RunViewerWidget(self.embedded_run_viewer_panel)
        self.embedded_run_viewer.closed.connect(self._close_embedded_viewer)
        self.embedded_run_viewer.stop_requested.connect(self.run_stop_requested.emit)
        run_viewer_layout.addWidget(self.embedded_run_viewer)
        self.editor_stack.addWidget(self.embedded_run_viewer_panel)

        right_panel = QFrame()
        right_panel.setObjectName("taskManagerRightPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self.editor_stack, 1)

        operation_layout = QHBoxLayout()
        operation_layout.setContentsMargins(14, 8, 14, 8)
        operation_layout.setSpacing(8)
        self.operation_context_label = QLabel("任务操作")
        self.operation_context_label.setObjectName("taskManagerOperationLabel")
        self.operation_context_label.hide()
        self.add_button = QPushButton("添加")
        self.add_button.setObjectName("settingsTestButton")
        self.add_button.clicked.connect(self._add_current)
        self.copy_button = QPushButton("复制")
        self.copy_button.setObjectName("settingsTestButton")
        self.copy_button.clicked.connect(self._duplicate_current)
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.clicked.connect(self._delete_current)
        self.run_action_button = QPushButton("运行")
        self.run_action_button.setObjectName("runActionButton")
        self.run_action_button.clicked.connect(self._run_current)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("quietButton")
        self.cancel_button.clicked.connect(self._cancel_current_editor)
        for button in (
            self.add_button,
            self.copy_button,
            self.delete_button,
            self.run_action_button,
            self.cancel_button,
        ):
            button.setFixedWidth(78)
            button.setMinimumHeight(40)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            operation_layout.addWidget(button)
        operation_layout.addStretch(1)
        self.dirty_label = QLabel("未保存")
        self.dirty_label.setObjectName("taskManagerDirtyLabel")
        operation_layout.addWidget(self.dirty_label)
        self.save_button = QPushButton("保存")
        self.save_button.setObjectName("taskManagerOperationSave")
        self.save_button.setMinimumHeight(40)
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save_current)
        operation_layout.addWidget(self.save_button)
        right_layout.addLayout(operation_layout)
        body.addWidget(right_panel, 1)

        root.addLayout(body, 1)
        self._update_operation_bar()


    @staticmethod
    def _card_title(text: str) -> QLabel:
        title = QLabel(text)
        title.setObjectName("settingsCardTitle")
        return title

    @staticmethod
    def _disable_context_menu(widget: QWidget) -> None:
        """Disable context menu for a widget."""
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

    def _mark_dirty(self, *_args: Any) -> None:
        """Mark the current editor as changed without reacting to reloads."""

        if self._suspend_dirty:
            return
        self._dirty = True
        self._update_operation_bar()

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = bool(dirty)
        self._update_operation_bar()

    def _current_operation_context(self) -> str:
        current = self.editor_stack.currentWidget()
        if current is self.embedded_action_editor_panel:
            return "embedded_action"
        if current is self.embedded_branch_steps_editor_panel:
            return "embedded_branch"
        if current in (
            self.embedded_json_viewer_panel,
            self.embedded_ui_tree_viewer_panel,
            self.embedded_run_viewer_panel,
        ):
            return "viewer"
        if self.left_tabs.currentIndex() == 0:
            return (
                "action"
                if self._creating_task or self.actions_list.currentRow() >= 0
                else "task"
            )
        return (
            "step"
            if self._creating_compound or self.compound_steps_list.currentRow() >= 0
            else "compound"
        )

    def _embedded_editor_has_changes(self) -> bool:
        current = self.editor_stack.currentWidget()
        if current is self.embedded_action_editor_panel:
            if self._embedded_editor_original is None:
                return False
            return self.embedded_action_editor.snapshot_data() != self._embedded_editor_original
        if current is self.embedded_branch_steps_editor_panel and self._branch_steps_stack:
            context = self._branch_steps_stack[-1]
            return self.embedded_branch_steps_editor.get_steps() != context.get(
                "original_steps", []
            )
        return False

    def _update_operation_bar(self) -> None:
        """Update the single bottom toolbar for the active object."""

        if not hasattr(self, "operation_context_label"):
            return
        context = self._current_operation_context()
        labels = {
            "task": "任务操作",
            "action": "动作操作",
            "compound": "复合任务操作",
            "step": "步骤操作",
            "embedded_action": "编辑动作",
            "embedded_branch": "编辑步骤",
            "viewer": "查看内容",
        }
        self.operation_context_label.setText(labels.get(context, "任务操作"))
        base_context = context in {"task", "compound"}
        action_context = context in {"action", "step", "embedded_branch"}
        editing_context = context in {"embedded_action", "embedded_branch"}
        has_selection = (
            self._selected_task is not None
            if context == "task"
            else self._selected_compound is not None
            if context == "compound"
            else self.actions_list.currentRow() >= 0
            if context == "action"
            else self.compound_steps_list.currentRow() >= 0
            if context == "step"
            else self.embedded_branch_steps_editor.steps_list.currentRow() >= 0
            if context == "embedded_branch"
            else False
        )
        for button in (self.add_button, self.copy_button, self.delete_button):
            button.setVisible(base_context or action_context)
        self.run_action_button.setVisible(action_context)
        self.cancel_button.setVisible(editing_context)
        self.copy_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)
        self.run_action_button.setEnabled(has_selection and action_context)
        self.add_button.setEnabled(base_context or action_context)
        self.save_button.setVisible(True)
        self.save_button.setEnabled(
            base_context or editing_context or self._dirty or self._embedded_editor_has_changes()
        )
        dirty = self._dirty or self._embedded_editor_has_changes()
        self.dirty_label.setVisible(dirty)

    def _add_current(self) -> None:
        context = self._current_operation_context()
        if context == "task":
            self._new_task()
        elif context == "compound":
            self._new_compound()
        elif context == "action":
            self._add_action()
        elif context == "step":
            self._add_step()
        elif context == "embedded_branch":
            self._add_branch_step()

    def _duplicate_current(self) -> None:
        context = self._current_operation_context()
        if context == "task":
            self._duplicate_task()
        elif context == "compound":
            self._duplicate_compound()
        elif context == "action":
            self._duplicate_action()
        elif context == "step":
            self._duplicate_step()
        elif context == "embedded_branch":
            self._duplicate_branch_step()

    def _delete_current(self) -> None:
        context = self._current_operation_context()
        if context == "task":
            self._delete_task()
        elif context == "compound":
            self._delete_compound()
        elif context == "action":
            self._delete_action()
        elif context == "step":
            self._delete_step()
        elif context == "embedded_branch":
            self._delete_branch_step()

    def _run_current(self) -> None:
        context = self._current_operation_context()
        if context in {"action", "embedded_branch"}:
            if context == "action":
                self._run_single_action()
            else:
                self._run_single_branch_step()
        elif context == "step":
            self._run_single_step()

    def _save_current(self, tab_index: int | None = None) -> bool:
        current = self.editor_stack.currentWidget()
        if current in (self.embedded_action_editor_panel, self.embedded_branch_steps_editor_panel):
            if not self._commit_embedded_editors():
                return False
        target_tab = self.left_tabs.currentIndex() if tab_index is None else tab_index
        if target_tab == 0:
            return self._save_task()
        return self._save_compound_from_editor()

    def _cancel_current_editor(self) -> None:
        if self.editor_stack.currentWidget() in (
            self.embedded_action_editor_panel,
            self.embedded_branch_steps_editor_panel,
        ):
            self._leave_embedded_editor()

    def _leave_all_embedded_editors(self) -> bool:
        """Return to the base editor before a list click changes context."""

        while self.editor_stack.currentWidget() in (
            self.embedded_action_editor_panel,
            self.embedded_branch_steps_editor_panel,
        ):
            if not self._leave_embedded_editor():
                return False
        return True

    def _ask_unsaved_changes(self, _operation: str) -> str:
        """Ask whether to discard the edits before continuing."""

        message_box = QMessageBox(self)
        message_box.setWindowTitle("未保存的修改")
        message_box.setText("当前内容尚未保存，确定放弃更改吗？")
        # Qt orders message-box buttons by role on Windows.  Use the roles
        # below so the visible order is explicitly: 取消 (left), 确认 (right).
        cancel_button = message_box.addButton("取消", QMessageBox.ButtonRole.AcceptRole)
        confirm_button = message_box.addButton("确认", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button.setObjectName("messageBoxAction")
        confirm_button.setObjectName("messageBoxAction")
        confirm_button.setDefault(True)
        message_box.setEscapeButton(cancel_button)
        message_box.setStyleSheet(_s.MESSAGE_BOX_QSS)
        message_box.exec()
        clicked_button = message_box.clickedButton()
        if clicked_button == confirm_button:
            return "discard"
        return "cancel"

    def _resolve_unsaved_changes(
        self, operation: str, tab_index: int | None = None
    ) -> bool:
        """Resolve the outer editor's dirty state before changing context."""

        if not self._dirty and not self._embedded_editor_has_changes():
            return True
        choice = self._ask_unsaved_changes(operation)
        if choice == "save":
            return self._save_current(tab_index)
        if choice == "discard":
            self._discard_current_changes(tab_index)
            return True
        return False

    def _discard_current_changes(self, tab_index: int | None = None) -> None:
        target_tab = self.left_tabs.currentIndex() if tab_index is None else tab_index
        self._dirty = False
        if target_tab == 0:
            task_id = self._selected_task
            if task_id in self._unsaved_task_ids:
                self._tasks.pop(str(task_id), None)
                self._unsaved_task_ids.discard(str(task_id))
                self._refresh_task_list()
            elif task_id and task_id in self._tasks:
                self._load_task_editor(self._tasks[task_id])
            else:
                self._clear_editor()
        else:
            name = self._selected_compound
            if name in self._unsaved_compound_names:
                self._unsaved_compound_names.discard(str(name))
                self._clear_compound_editor()
                self._refresh_compound_list()
            elif name and name in self._compound_library:
                self._load_compound_editor(self._compound_library[name])
            else:
                self._clear_compound_editor()
        self._update_operation_bar()


    # ------------------------------------------------------------ data

    def reload(self) -> None:
        """Re-read settings, task files and the compound library."""
        self.settings = load_settings(self.settings_path)
        tasks_directory = self.base_directory / "config" / "tasks"
        valid_ids: set[str] = set()
        try:
            validated_tasks, _errors = load_task_directory(
                tasks_directory,
                variables={"qq_group_name": self.settings.get("qq_group_name", "")},
            )
            valid_ids = {task.id for task in validated_tasks}
        except (FileNotFoundError, OSError, ValueError):
            valid_ids = set()
        loaded: dict[str, dict[str, Any]] = {}
        for task_path in sorted(tasks_directory.glob("*.json")):
            try:
                data = load_json(task_path)
            except (OSError, ValueError):
                continue
            if (
                isinstance(data, dict)
                and isinstance(data.get("id"), str)
                and data["id"] in valid_ids
            ):
                loaded[data["id"]] = data
        ordered_ids: list[str] = []
        saved_order = self.settings.get("task_order")
        if isinstance(saved_order, list):
            for task_id in saved_order:
                if task_id in loaded and task_id not in ordered_ids:
                    ordered_ids.append(task_id)
        for task_id in loaded:
            if task_id not in ordered_ids:
                ordered_ids.append(task_id)
        self._tasks = {task_id: loaded[task_id] for task_id in ordered_ids}
        actions_directory = self.base_directory / "config" / "actions"
        library: dict[str, dict[str, Any]] = {}
        for action_path in sorted(actions_directory.glob("*.json")):
            try:
                data = load_json(action_path)
            except (OSError, ValueError):
                continue
            if (
                isinstance(data, dict)
                and isinstance(data.get("name"), str)
                and data["name"].strip()
            ):
                library[data["name"].strip()] = data
        self._compound_library = library
        self._unsaved_task_ids.clear()
        self._unsaved_compound_names.clear()
        self._creating_task = False
        self._creating_compound = False
        self._suspend_dirty = True
        self._dirty = False

        cleanup_index = self.cleanup_mode_combo.findData(
            str(self.settings.get("cleanup_mode", "recycle"))
        )
        self.cleanup_mode_combo.blockSignals(True)
        self.cleanup_mode_combo.setCurrentIndex(max(0, cleanup_index))
        self.cleanup_mode_combo.blockSignals(False)

        self._refresh_task_list()
        self._refresh_compound_list()
        self._embedded_editor_original = None
        self._branch_steps_stack.clear()
        self._suspend_dirty = False
        self._update_operation_bar()


        try:
            save_settings(self.settings_path, self.settings)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.tasks_changed.emit()

    def _refresh_task_list(self, select_id: str | None = None) -> None:
        self.task_list.blockSignals(True)
        self.task_list.clear()
        for task_id, data in self._tasks.items():
            name = str(data.get("name", ""))
            item = QListWidgetItem(f"{name}（{task_id}）")
            item.setData(Qt.ItemDataRole.UserRole, task_id)
            self.task_list.addItem(item)
        self.task_list.blockSignals(False)
        if select_id is not None:
            for index in range(self.task_list.count()):
                if self.task_list.item(index).data(Qt.ItemDataRole.UserRole) == select_id:
                    self.task_list.setCurrentRow(index)
                    return
        if self.task_list.count():
            self.task_list.setCurrentRow(0)
        else:
            self._clear_editor()
        self._update_operation_bar()

    def _on_task_selected(self, row: int) -> None:
        item = self.task_list.item(row)
        task_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        next_id = str(task_id) if task_id else None
        if next_id == self._selected_task:
            self._update_operation_bar()
            return
        previous_id = self._selected_task
        if not self._resolve_unsaved_changes("切换任务"):
            self._restore_task_selection(previous_id)
            return
        self._selected_task = next_id
        data = self._tasks.get(self._selected_task or "")
        if data is None:
            self._clear_editor()
            return
        self._load_task_editor(data)
        self.editor_stack.setCurrentWidget(self.editor_stack.widget(0))

    def _on_task_item_clicked(self, item: QListWidgetItem) -> None:
        """Focus the task itself when its already-selected row is clicked."""

        task_id = item.data(Qt.ItemDataRole.UserRole)
        if str(task_id or "") != str(self._selected_task or ""):
            return
        if not self._leave_all_embedded_editors():
            return
        self.actions_list.setCurrentRow(-1)
        self._embedded_previous_panel = None
        self.editor_stack.setCurrentWidget(self.editor_stack.widget(0))
        self._update_operation_bar()

    def _restore_task_selection(self, task_id: str | None) -> None:
        self.task_list.blockSignals(True)
        try:
            if task_id is None:
                self.task_list.setCurrentRow(-1)
            else:
                for index in range(self.task_list.count()):
                    if self.task_list.item(index).data(Qt.ItemDataRole.UserRole) == task_id:
                        self.task_list.setCurrentRow(index)
                        break
        finally:
            self.task_list.blockSignals(False)
        self._update_operation_bar()

    def _load_task_editor(self, data: dict[str, Any]) -> None:
        self._creating_task = False
        self._suspend_dirty = True
        try:
            self.task_id_edit.setText(str(data.get("id", "")))
            self.task_name_edit.setText(str(data.get("name", "")))
            self.task_package_edit.setText(str(data.get("package", "")))
            actions = data.get("actions", [])
            self._actions_buffer = (
                deep_copy(actions) if isinstance(actions, list) else []
            )
            self._refresh_actions_list()
        finally:
            self._suspend_dirty = False
        self._dirty = False
        self._update_operation_bar()

    def _clear_editor(self) -> None:
        self._creating_task = False
        self._suspend_dirty = True
        self._selected_task = None
        self._actions_buffer = []
        self.task_id_edit.clear()
        self.task_name_edit.clear()
        self.task_package_edit.clear()
        self._refresh_actions_list()
        self._suspend_dirty = False
        self._dirty = False
        self._update_operation_bar()

    # ------------------------------------------------------------ tasks

    def _new_task(self) -> None:
        if not self._resolve_unsaved_changes("新建任务"):
            return
        self.task_list.setCurrentRow(-1)
        self._clear_editor()
        self._creating_task = True
        self._set_dirty(False)

    def _duplicate_task(self) -> None:
        if not self._resolve_unsaved_changes("复制任务"):
            return
        source_id = self._selected_task
        if not source_id or source_id not in self._tasks:
            QMessageBox.information(self, "复制任务", "请先选择一个任务。")
            return
        data = deep_copy(self._tasks[source_id])
        candidate = f"{source_id}_copy"
        while candidate in self._tasks:
            candidate = f"{candidate}_copy"
        data["id"] = candidate
        self._tasks[candidate] = data
        self._unsaved_task_ids.add(candidate)
        self._refresh_task_list(select_id=candidate)
        self._set_dirty(True)

    def _task_path_for_id(self, task_id: str) -> Path | None:
        tasks_directory = self.base_directory / "config" / "tasks"
        direct_path = tasks_directory / f"{task_id}.json"
        if direct_path.exists():
            return direct_path
        for task_path in tasks_directory.glob("*.json"):
            try:
                data = load_json(task_path)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("id") == task_id:
                return task_path
        return None

    def _delete_task(self) -> None:
        if not self._resolve_unsaved_changes("删除任务"):
            return
        task_id = self._selected_task
        if not task_id or task_id not in self._tasks:
            QMessageBox.information(self, "删除任务", "请先选择一个任务。")
            return
        if not self._delete_file(
            self._task_path_for_id(task_id), "删除任务", "任务", task_id
        ):
            return
        self._tasks.pop(task_id, None)
        self._unsaved_task_ids.discard(task_id)
        task_order = self.settings.get("task_order")
        if not isinstance(task_order, list):
            task_order = []
        self.settings["task_order"] = [item for item in task_order if item != task_id]
        try:
            save_settings(self.settings_path, self.settings)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "顺序保存失败", f"任务已删除，但任务顺序未持久化：{exc}")
        self._dirty = False
        self._refresh_task_list()
        self.tasks_changed.emit()
        self._update_operation_bar()

    def _delete_file(self, path: Path | None, title: str, kind_label: str, name: str) -> bool:
        """Confirm and delete ``path`` under the current cleanup mode.

        When ``path`` is None or does not exist, deletion is a no-op but still
        counts as success. Returns False when the user cancels or the deletion
        fails, so callers may abort the rest of the removal flow.
        """
        mode = str(self.cleanup_mode_combo.currentData() or "recycle")
        mode_label = "永久删除" if mode == "permanent" else "删除至回收站"
        if not confirm_dialog(self, title, f"确定{mode_label}{kind_label} {name}？"):
            return False
        if path is not None and path.exists():
            try:
                if mode == "permanent":
                    path.unlink()
                else:
                    send_to_recycle_bin(path)
            except (OSError, TrashError) as exc:
                QMessageBox.warning(self, "删除失败", str(exc))
                return False
        return True

    def _on_cleanup_mode_changed(self, _index: int) -> None:
        mode = str(self.cleanup_mode_combo.currentData() or "recycle")
        self.settings["cleanup_mode"] = mode
        try:
            save_settings(self.settings_path, self.settings)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    # ------------------------------------------------------------ compound library

    def _refresh_compound_list(self, select_name: str | None = None) -> None:
        self.compound_list.blockSignals(True)
        self.compound_list.clear()
        for name, data in self._compound_library.items():
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.compound_list.addItem(item)
        self.compound_list.blockSignals(False)
        if select_name is not None:
            for index in range(self.compound_list.count()):
                if self.compound_list.item(index).data(Qt.ItemDataRole.UserRole) == select_name:
                    self.compound_list.setCurrentRow(index)
                    return
        if self.compound_list.count():
            self.compound_list.setCurrentRow(0)
        else:
            self._selected_compound = None

    def _on_compound_selected(self, row: int) -> None:
        item = self.compound_list.item(row)
        name = item.data(Qt.ItemDataRole.UserRole) if item else None
        next_name = str(name) if name else None
        if next_name == self._selected_compound:
            self._update_operation_bar()
            return
        previous_name = self._selected_compound
        if not self._resolve_unsaved_changes("切换复合任务"):
            self._restore_compound_selection(previous_name)
            return
        self._selected_compound = next_name
        data = self._compound_library.get(self._selected_compound or "")
        if data is None:
            self._clear_compound_editor()
            return
        self._load_compound_editor(data)
        self.editor_stack.setCurrentWidget(self.editor_stack.widget(1))

    def _on_compound_item_clicked(self, item: QListWidgetItem) -> None:
        """Focus the compound task when its already-selected row is clicked."""

        name = item.data(Qt.ItemDataRole.UserRole)
        if str(name or "") != str(self._selected_compound or ""):
            return
        if not self._leave_all_embedded_editors():
            return
        self.compound_steps_list.setCurrentRow(-1)
        self._embedded_previous_panel = None
        self.editor_stack.setCurrentWidget(self.editor_stack.widget(1))
        self._update_operation_bar()

    def _restore_compound_selection(self, name: str | None) -> None:
        self.compound_list.blockSignals(True)
        try:
            if name is None:
                self.compound_list.setCurrentRow(-1)
            else:
                for index in range(self.compound_list.count()):
                    if self.compound_list.item(index).data(Qt.ItemDataRole.UserRole) == name:
                        self.compound_list.setCurrentRow(index)
                        break
        finally:
            self.compound_list.blockSignals(False)
        self._update_operation_bar()

    def _new_compound(self) -> None:
        if not self._resolve_unsaved_changes("新建复合任务"):
            return
        self.compound_list.setCurrentRow(-1)
        self._clear_compound_editor()
        self._creating_compound = True
        self._set_dirty(False)

    def _duplicate_compound(self) -> None:
        if not self._resolve_unsaved_changes("复制复合任务"):
            return
        source_name = self._selected_compound
        if not source_name or source_name not in self._compound_library:
            QMessageBox.information(self, "复制复合任务", "请先选择一个复合任务。")
            return
        data = deep_copy(self._compound_library[source_name])
        candidate = f"{source_name}_copy"
        while candidate in self._compound_library:
            candidate = f"{candidate}_copy"
        data["name"] = candidate
        self.compound_list.setCurrentRow(-1)
        self._selected_compound = None
        self._load_compound_editor(data)
        self._unsaved_compound_names.add(candidate)
        self._set_dirty(True)

    def _edit_compound(self) -> None:
        item = self.compound_list.currentItem()
        name = item.data(Qt.ItemDataRole.UserRole) if item else None
        data = self._compound_library.get(str(name or ""))
        if data is None:
            QMessageBox.information(self, "编辑复合任务", "请先选择一个复合任务。")
            return
        self._load_compound_editor(data)

    def _delete_compound(self) -> None:
        if not self._resolve_unsaved_changes("删除复合任务"):
            return
        item = self.compound_list.currentItem()
        raw_name = item.data(Qt.ItemDataRole.UserRole) if item else None
        name = str(raw_name or "")
        if name not in self._compound_library:
            QMessageBox.information(self, "删除复合任务", "请先选择一个复合任务。")
            return
        if not self._delete_file(
            self.base_directory / "config" / "actions" / f"{name}.json",
            "删除复合任务",
            "复合任务",
            name,
        ):
            return
        self._compound_library.pop(name, None)
        self._unsaved_compound_names.discard(name)
        self._dirty = False
        self._refresh_compound_list()
        self.tasks_changed.emit()
        self._update_operation_bar()

    @staticmethod
    def _replace_compound_reference(value: Any, old_name: str, new_name: str) -> bool:
        changed = False
        if isinstance(value, dict):
            if value.get("type") == COMPOUND_TYPE and value.get("name") == old_name:
                value["name"] = new_name
                changed = True
            for child in value.values():
                if TaskManagerWidget._replace_compound_reference(
                    child, old_name, new_name
                ):
                    changed = True
        elif isinstance(value, list):
            for child in value:
                if TaskManagerWidget._replace_compound_reference(
                    child, old_name, new_name
                ):
                    changed = True
        return changed

    def _compound_reference_updates(
        self, old_name: str, new_name: str
    ) -> list[tuple[str, dict[str, Any], dict[str, Any], Path | None]]:
        updates: list[tuple[str, dict[str, Any], dict[str, Any], Path | None]] = []
        for task_id, original in self._tasks.items():
            candidate = deep_copy(original)
            if not self._replace_compound_reference(candidate, old_name, new_name):
                continue
            updates.append(
                (
                    task_id,
                    original,
                    candidate,
                    self._task_path_for_id(task_id),
                )
            )
        return updates

    @staticmethod
    def _write_json_file(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _save_compound(
        self, data: dict[str, Any], previous_name: str | None = None
    ) -> bool:
        name = str(data.get("name", "")).strip()
        steps = data.get("steps")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
            QMessageBox.warning(self, "无法保存", "复合任务名只能包含字母、数字、下划线和短横线，且以字母或数字开头。")
            return False
        if not isinstance(steps, list) or not steps:
            QMessageBox.warning(self, "无法保存", "复合任务 steps 必须是非空列表。")
            return False
        action_path = self.base_directory / "config" / "actions" / f"{name}.json"
        if previous_name != name and (name in self._compound_library or action_path.exists()):
            QMessageBox.warning(self, "无法保存", f"复合任务名已存在：{name}，请改名后重试。")
            return False
        if previous_name and previous_name in self._compound_library:
            old_data = deep_copy(self._compound_library[previous_name])
        else:
            old_data = None
        old_path = (
            self.base_directory / "config" / "actions" / f"{previous_name}.json"
            if previous_name and previous_name != name
            else None
        )
        reference_updates = (
            self._compound_reference_updates(previous_name, name)
            if old_path is not None and previous_name
            else []
        )
        written_references: list[tuple[Path, dict[str, Any]]] = []
        try:
            for task_id, original, updated, task_path in reference_updates:
                if task_path is None or task_id in self._unsaved_task_ids:
                    continue
                self._write_json_file(task_path, updated)
                written_references.append((task_path, original))
            self._write_json_file(action_path, data)
        except OSError as exc:
            for task_path, original in written_references:
                try:
                    self._write_json_file(task_path, original)
                except OSError:
                    pass
            QMessageBox.warning(self, "无法保存", str(exc))
            return False
        if old_path is not None and old_path.exists():
            mode = str(self.cleanup_mode_combo.currentData() or "recycle")
            try:
                if mode == "permanent":
                    old_path.unlink()
                else:
                    send_to_recycle_bin(old_path)
            except (OSError, TrashError) as exc:
                rollback_error: Exception | None = None
                try:
                    if mode == "permanent":
                        action_path.unlink()
                    else:
                        send_to_recycle_bin(action_path)
                except (OSError, TrashError) as rollback_exc:
                    rollback_error = rollback_exc
                if old_data is not None and previous_name is not None:
                    self._compound_library[previous_name] = old_data
                for task_path, original in written_references:
                    try:
                        self._write_json_file(task_path, original)
                    except OSError:
                        pass
                self._refresh_compound_list()
                if rollback_error is None:
                    message = f"重命名失败，已回滚：{exc}"
                else:
                    message = f"重命名失败，回滚删除新文件也失败：{exc}；{rollback_error}，新旧两个文件可能都存在。"
                QMessageBox.warning(self, "保存失败", message)
                return False
        if previous_name and previous_name != name:
            self._compound_library.pop(previous_name, None)
            self._unsaved_compound_names.discard(previous_name)
        for task_id, _original, updated, _task_path in reference_updates:
            self._tasks[task_id] = updated
        self._unsaved_compound_names.discard(name)
        self._compound_library[name] = data
        self._selected_compound = name
        self._creating_compound = False
        self._dirty = False
        self._refresh_compound_list(select_name=name)
        self.tasks_changed.emit()
        self._update_operation_bar()
        return True

    def _save_compound_from_editor(self) -> bool:
        data = {
            "name": self.compound_name_edit.text().strip(),
            "params": list(self._params_buffer),
            "steps": deep_copy(self._steps_buffer),
        }
        if self._compound_description_present or self._compound_description_buffer:
            data["description"] = self._compound_description_buffer
        return self._save_compound(data, previous_name=self._selected_compound)

    def _view_compound_json(self) -> None:
        data = {
            "name": self.compound_name_edit.text().strip(),
            "params": list(self._params_buffer),
            "steps": deep_copy(self._steps_buffer),
        }
        self.embedded_json_viewer.load_data(data)
        self._embedded_previous_panel = self.editor_stack.currentWidget()
        self.editor_stack.setCurrentWidget(self.embedded_json_viewer_panel)

    def _load_compound_editor(self, data: dict[str, Any]) -> None:
        self._creating_compound = False
        self._suspend_dirty = True
        self.compound_name_edit.setText(str(data.get("name", "")))
        self._compound_description_buffer = str(data.get("description", ""))
        self._compound_description_present = "description" in data
        raw_params = data.get("params")
        self._params_buffer = (
            [str(item).strip() for item in raw_params if isinstance(item, str) and item.strip()]
            if isinstance(raw_params, list)
            else []
        )
        raw_steps = data.get("steps")
        self._steps_buffer = (
            deep_copy(raw_steps) if isinstance(raw_steps, list) else []
        )
        self._refresh_steps_list()
        self._suspend_dirty = False
        self._dirty = False
        self._update_operation_bar()

    def _clear_compound_editor(self) -> None:
        self._creating_compound = False
        self._suspend_dirty = True
        self._selected_compound = None
        self._params_buffer = []
        self._steps_buffer = []
        self._compound_description_buffer = ""
        self._compound_description_present = False
        self.compound_name_edit.clear()
        self._refresh_steps_list()
        self._suspend_dirty = False
        self._dirty = False
        self._update_operation_bar()

    def _refresh_steps_list(self) -> None:
        self._refresh_list(self.compound_steps_list, self._steps_buffer)

    def _sync_steps_from_list(self) -> None:
        """Reorder _steps_buffer to match the visual order after drag-drop."""
        self._sync_list_order(self.compound_steps_list, self._steps_buffer)

    def _on_compound_steps_moved(self) -> None:
        """Sync buffer after internal drag-drop reorder."""
        self._sync_steps_from_list()
        self._mark_dirty()

    def _add_step(self) -> None:
        self._show_embedded_editor("compound_step", index=-1, initial={})

    def _edit_step(self) -> None:
        self._edit_entry(
            self.compound_steps_list, self._steps_buffer, "compound_step", "步骤"
        )

    def _delete_step(self) -> None:
        self._delete_entry(
            self.compound_steps_list, self._steps_buffer, self._refresh_steps_list
        )

    def _duplicate_step(self) -> None:
        self._duplicate_entry(
            self.compound_steps_list,
            self._steps_buffer,
            self._refresh_steps_list,
            "步骤",
        )

    def _run_single_step(self) -> None:
        """Run only the selected step."""
        self._run_single_entry(
            self._steps_buffer,
            self.compound_steps_list.currentRow(),
            name_edit=self.compound_name_edit,
            kind_label="步骤",
            default_name="单步骤测试",
        )

    def _show_embedded_editor(
        self,
        mode: str,
        index: int = -1,
        initial: dict[str, Any] | None = None,
        return_panel: QWidget | None = None,
    ) -> None:
        """Show the embedded action editor in the right panel."""
        self._embedded_editor_mode = mode
        self._embedded_editor_index = index
        self._embedded_editor_return_panel = return_panel
        self._embedded_editor_original = deep_copy(initial or {})
        allow_compound = mode == "task_action"
        self.embedded_action_editor._allow_compound = allow_compound
        self.embedded_action_editor._compound_library = dict(self._compound_library)
        if initial is None:
            initial = {}
        self._suspend_dirty = True
        try:
            self.embedded_action_editor.load_data(initial)
        finally:
            self._suspend_dirty = False
        self.editor_stack.setCurrentWidget(self.embedded_action_editor_panel)
        self._update_operation_bar()

    def _open_branch_steps_editor(
        self,
        key: str,
        steps: list[dict[str, Any]],
    ) -> None:
        action_data = self.embedded_action_editor.snapshot_data()
        action_data[key] = deep_copy(steps) if isinstance(steps, list) else []
        self._branch_steps_stack.append(
            {
                "key": key,
                "action_data": action_data,
                "steps": (
                    deep_copy(steps) if isinstance(steps, list) else []
                ),
                "original_steps": (
                    deep_copy(steps) if isinstance(steps, list) else []
                ),
            }
        )
        self.embedded_branch_steps_editor.load_steps(
            self._branch_steps_stack[-1]["steps"]
        )
        self.editor_stack.setCurrentWidget(self.embedded_branch_steps_editor_panel)
        self._update_operation_bar()

    def _on_branch_steps_saved(self, steps: list[dict[str, Any]]) -> None:
        if self._branch_steps_stack:
            context = self._branch_steps_stack.pop()
            context["action_data"][context["key"]] = deep_copy(steps)
            self._suspend_dirty = True
            try:
                self.embedded_action_editor.load_data(context["action_data"])
            finally:
                self._suspend_dirty = False
            self._mark_dirty()
        self.editor_stack.setCurrentWidget(self.embedded_action_editor_panel)
        self._update_operation_bar()

    def _on_branch_steps_cancelled(self) -> None:
        if self._branch_steps_stack:
            context = self._branch_steps_stack.pop()
            self._suspend_dirty = True
            try:
                self.embedded_action_editor.load_data(context["action_data"])
            finally:
                self._suspend_dirty = False
        self.editor_stack.setCurrentWidget(self.embedded_action_editor_panel)
        self._update_operation_bar()

    def _add_branch_step(self) -> None:
        if self._branch_steps_stack:
            self._branch_steps_stack[-1]["steps"] = (
                self.embedded_branch_steps_editor.get_steps()
            )
        self._show_embedded_editor(
            "branch_step",
            index=-1,
            initial={},
            return_panel=self.embedded_branch_steps_editor_panel,
        )

    def _duplicate_branch_step(self) -> None:
        row = self.embedded_branch_steps_editor.steps_list.currentRow()
        steps = self.embedded_branch_steps_editor.get_steps()
        if row < 0 or row >= len(steps):
            QMessageBox.information(self, "复制步骤", "请先选择一个步骤。")
            return
        steps.insert(row + 1, deep_copy(steps[row]))
        self.embedded_branch_steps_editor.load_steps(steps)
        if self._branch_steps_stack:
            self._branch_steps_stack[-1]["steps"] = deep_copy(steps)
        self.embedded_branch_steps_editor.steps_list.setCurrentRow(row + 1)
        self._mark_dirty()

    def _delete_branch_step(self) -> None:
        row = self.embedded_branch_steps_editor.steps_list.currentRow()
        steps = self.embedded_branch_steps_editor.get_steps()
        if row < 0 or row >= len(steps):
            return
        del steps[row]
        self.embedded_branch_steps_editor.load_steps(steps)
        if self._branch_steps_stack:
            self._branch_steps_stack[-1]["steps"] = deep_copy(steps)
        self._mark_dirty()

    def _run_single_branch_step(self) -> None:
        self._run_single_entry(
            self.embedded_branch_steps_editor.get_steps(),
            self.embedded_branch_steps_editor.steps_list.currentRow(),
            name_edit=self.task_name_edit,
            kind_label="步骤",
            default_name="单步骤测试",
        )

    def _edit_branch_step(self, row: int) -> None:
        if not self._branch_steps_stack:
            return
        context = self._branch_steps_stack[-1]
        context["steps"] = self.embedded_branch_steps_editor.get_steps()
        steps = context["steps"]
        if row < 0 or row >= len(steps):
            return
        self._show_embedded_editor(
            "branch_step",
            index=row,
            initial=steps[row],
            return_panel=self.embedded_branch_steps_editor_panel,
        )

    def _on_embedded_editor_saved(self, data: dict[str, Any]) -> None:
        """Handle save from embedded editor."""
        return_panel: QWidget | None = None
        if self._embedded_editor_mode == "task_action":
            if self._embedded_editor_index >= 0:
                self._actions_buffer[self._embedded_editor_index] = data
            else:
                self._actions_buffer.append(data)
            self._refresh_actions_list()
            if self._embedded_editor_index >= 0:
                self.actions_list.setCurrentRow(self._embedded_editor_index)
            else:
                self.actions_list.setCurrentRow(len(self._actions_buffer) - 1)
        elif self._embedded_editor_mode == "compound_step":
            if self._embedded_editor_index >= 0:
                self._steps_buffer[self._embedded_editor_index] = data
            else:
                self._steps_buffer.append(data)
            self._refresh_steps_list()
            if self._embedded_editor_index >= 0:
                self.compound_steps_list.setCurrentRow(self._embedded_editor_index)
            else:
                self.compound_steps_list.setCurrentRow(len(self._steps_buffer) - 1)
        elif self._embedded_editor_mode == "branch_step":
            if self._branch_steps_stack:
                context = self._branch_steps_stack[-1]
                if self._embedded_editor_index >= 0:
                    context["steps"][self._embedded_editor_index] = data
                else:
                    context["steps"].append(data)
                self.embedded_branch_steps_editor.load_steps(context["steps"])
                return_panel = self.embedded_branch_steps_editor_panel
            else:
                return_panel = self._embedded_editor_return_panel
        else:
            return_panel = self._embedded_editor_return_panel
        self._embedded_editor_mode = None
        self._embedded_editor_index = -1
        self._embedded_editor_return_panel = None
        self._embedded_editor_original = None
        self._mark_dirty()
        target_panel = return_panel
        if target_panel is None:
            target_panel = self.editor_stack.widget(self.left_tabs.currentIndex())
        self.editor_stack.setCurrentWidget(target_panel)
        self._update_operation_bar()

    def _on_embedded_editor_cancelled(self) -> None:
        """Handle cancel from embedded editor."""
        return_panel = self._embedded_editor_return_panel
        self._embedded_editor_mode = None
        self._embedded_editor_index = -1
        self._embedded_editor_return_panel = None
        self._embedded_editor_original = None
        target_panel = return_panel
        if target_panel is None:
            target_panel = self.editor_stack.widget(self.left_tabs.currentIndex())
        self.editor_stack.setCurrentWidget(target_panel)
        self._update_operation_bar()

    def _close_embedded_viewer(self) -> None:
        """Return from an embedded viewer to the panel shown before it."""
        target_panel = self._embedded_previous_panel
        self._embedded_previous_panel = None
        if target_panel is not None:
            self.editor_stack.setCurrentWidget(target_panel)
        else:
            self.editor_stack.setCurrentIndex(self.left_tabs.currentIndex())

    def _commit_embedded_editors(self) -> bool:
        """Commit nested embedded editors before persisting the parent object."""

        for _ in range(self._MAX_EMBEDDED_EDITOR_DEPTH):
            current = self.editor_stack.currentWidget()
            if current is self.embedded_branch_steps_editor_panel:
                self._on_branch_steps_saved(
                    self.embedded_branch_steps_editor.get_steps()
                )
                continue
            if current is self.embedded_action_editor_panel:
                data = self.embedded_action_editor.collect()
                if data is None:
                    return False
                self._on_embedded_editor_saved(data)
                continue
            break
        return True

    def _leave_embedded_editor(self) -> bool:
        """Leave one embedded editing level after resolving pending changes."""

        current = self.editor_stack.currentWidget()
        if current is self.embedded_branch_steps_editor_panel:
            changed = self._embedded_editor_has_changes()
            if changed:
                choice = self._ask_unsaved_changes("离开步骤编辑")
                if choice == "save":
                    self._on_branch_steps_saved(
                        self.embedded_branch_steps_editor.get_steps()
                    )
                elif choice == "discard":
                    self._on_branch_steps_cancelled()
                else:
                    return False
            else:
                self._on_branch_steps_cancelled()
            return True
        if current is self.embedded_action_editor_panel:
            changed = self._embedded_editor_has_changes()
            if changed:
                choice = self._ask_unsaved_changes("离开动作编辑")
                if choice == "save":
                    data = self.embedded_action_editor.collect()
                    if data is None:
                        return False
                    self._on_embedded_editor_saved(data)
                elif choice == "discard":
                    self._on_embedded_editor_cancelled()
                else:
                    return False
            else:
                self._on_embedded_editor_cancelled()
            return True
        return False

    def has_unsaved_changes(self) -> bool:
        """True while the editor holds modifications that are not persisted."""
        return self._dirty or self._embedded_editor_has_changes()

    def go_back(self) -> bool:
        """Navigate one level back while protecting unsaved editor changes.

        Returns True when the back press was consumed (dialog shown / a level
        popped) so callers must not leave the page, and False when there is
        nothing to protect and the page may close.
        """
        current = self.editor_stack.currentWidget()
        if current in (
            self.embedded_action_editor_panel,
            self.embedded_branch_steps_editor_panel,
        ):
            self._leave_embedded_editor()
            return True
        if current in {
            self.embedded_json_viewer_panel,
            self.embedded_ui_tree_viewer_panel,
            self.embedded_run_viewer_panel,
        }:
            self._close_embedded_viewer()
            return True
        if self._dirty:
            return not self._resolve_unsaved_changes("返回任务管理")
        return False

    def show_run_viewer(self, task_name: str) -> None:
        """Open the embedded run output panel for a debug run."""
        self.embedded_run_viewer.start_run(task_name)
        self._embedded_previous_panel = self.editor_stack.currentWidget()
        self.editor_stack.setCurrentWidget(self.embedded_run_viewer_panel)

    def append_run_log(self, message: str) -> None:
        """Append a worker log line to the embedded run output."""
        self.embedded_run_viewer.append_log(message)

    def set_run_progress(self, index: int, total: int, description: str) -> None:
        """Update progress inside the embedded run output."""
        self.embedded_run_viewer.set_progress(index, total, description)

    def finish_run(self, result: RunResult) -> None:
        """Show the final result inside the embedded run output."""
        self.embedded_run_viewer.finish_run(result)

    def abort_run(self, message: str) -> None:
        """Mark the embedded run output as failed before execution starts."""
        self.embedded_run_viewer.abort_run(message)

    def _on_embedded_ui_tree_action_inserted(self, action: dict[str, Any]) -> None:
        """Handle action insertion from embedded UI tree viewer."""
        current_tab = self.left_tabs.currentIndex()
        if current_tab == 0:
            self._actions_buffer.append(action)
            self._refresh_actions_list()
            self.actions_list.setCurrentRow(len(self._actions_buffer) - 1)
        else:
            self._steps_buffer.append(action)
            self._refresh_steps_list()
            self.compound_steps_list.setCurrentRow(len(self._steps_buffer) - 1)
        self.editor_stack.setCurrentIndex(current_tab)

    def _on_embedded_ui_tree_try_click_requested(self, x: int, y: int, _label: str) -> None:
        """Tap the selected node once through the ADB session used for the dump."""
        if self._ui_tree_adb is None:
            QMessageBox.warning(self, "无法尝试点击", "ADB 未连接，请先抓取 UI 树。")
            return
        try:
            self._ui_tree_adb.tap(x, y)
        except Exception as exc:
            QMessageBox.warning(self, "尝试点击失败", str(exc))
            return

    def _save_task(self) -> bool:
        task_id = self.task_id_edit.text().strip()
        name = self.task_name_edit.text().strip()
        package = self.task_package_edit.text().strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", task_id):
            QMessageBox.warning(self, "无法保存", "任务 id 只能包含字母、数字、下划线和短横线，且以字母或数字开头。")
            return False
        if not name:
            QMessageBox.warning(self, "无法保存", "任务名称不能为空。")
            return False
        if not package:
            QMessageBox.warning(self, "无法保存", "任务包名不能为空。")
            return False
        actions = deep_copy(self._actions_buffer)
        if not actions:
            QMessageBox.warning(self, "无法保存", "任务至少需要一个动作。")
            return False
        previous_id = self._selected_task
        target = self.base_directory / "config" / "tasks" / f"{task_id}.json"
        if previous_id != task_id and (task_id in self._tasks or target.exists()):
            QMessageBox.warning(self, "无法保存", f"任务 id 已存在（文件已存在）：{task_id}\n请改名后重试。")
            return False
        validation_errors = self._validate_task_actions(actions)
        if validation_errors:
            QMessageBox.warning(self, "无法保存", "动作校验失败：\n" + "\n".join(validation_errors))
            return False
        old_data = (
            deep_copy(self._tasks[previous_id])
            if previous_id and previous_id in self._tasks
            else None
        )
        data = {
            "id": task_id,
            "name": name,
            "package": package,
            "actions": actions,
        }
        if old_data is not None and "description" in old_data:
            data["description"] = old_data["description"]
        try:
            TaskDefinition.from_dict(data)
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return False
        old_path = self._task_path_for_id(previous_id) if previous_id else None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return False
        if previous_id and previous_id != task_id and old_path is not None:
            mode = str(self.cleanup_mode_combo.currentData() or "recycle")
            try:
                if mode == "permanent":
                    old_path.unlink()
                else:
                    send_to_recycle_bin(old_path)
            except (OSError, TrashError) as exc:
                rollback_error: Exception | None = None
                try:
                    if mode == "permanent":
                        target.unlink()
                    else:
                        send_to_recycle_bin(target)
                except (OSError, TrashError) as rollback_exc:
                    rollback_error = rollback_exc
                if old_data is not None:
                    self._tasks[previous_id] = old_data
                self._selected_task = previous_id
                self._refresh_task_list(select_id=previous_id)
                if rollback_error is None:
                    message = f"重命名失败，已回滚：{exc}"
                else:
                    message = f"重命名失败，回滚删除新文件也失败：{exc}；{rollback_error}，新旧两个文件可能都存在。"
                QMessageBox.warning(self, "保存失败", message)
                return False
        if previous_id and previous_id != task_id:
            self._tasks.pop(previous_id, None)
            self._unsaved_task_ids.discard(previous_id)
        self._tasks[task_id] = data
        self._unsaved_task_ids.discard(task_id)
        task_order = self.settings.get("task_order")
        if not isinstance(task_order, list):
            task_order = []
        task_order = list(task_order)
        if previous_id and previous_id != task_id:
            if previous_id in task_order:
                task_order[task_order.index(previous_id)] = task_id
            elif task_id not in task_order:
                task_order.append(task_id)
        elif task_id not in task_order:
            task_order.append(task_id)
        self.settings["task_order"] = task_order
        execution_counts = self.settings.get("task_execution_counts")
        if isinstance(execution_counts, dict) and previous_id and previous_id != task_id:
            if previous_id in execution_counts:
                execution_counts[task_id] = execution_counts.pop(previous_id)
        try:
            save_settings(self.settings_path, self.settings)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "顺序保存失败", f"任务已保存，但任务顺序未持久化：{exc}")
        self._selected_task = task_id
        self._creating_task = False
        self._dirty = False
        self._refresh_task_list(select_id=task_id)
        self.tasks_changed.emit()
        self._update_operation_bar()
        return True

    def _validate_task_actions(self, actions: list[dict[str, Any]]) -> list[str]:
        errors: list[str] = []
        for index, data in enumerate(actions, start=1):
            if not isinstance(data, dict):
                errors.append(f"动作 {index} 必须是对象")
                continue
            try:
                action = Action.from_dict(data)
            except ValueError as exc:
                errors.append(f"动作 {index}: {exc}")
                continue
            if action.type == COMPOUND_TYPE:
                name = str(action.parameters.get("name", "")).strip()
                if not name:
                    errors.append(f"动作 {index}: 复合动作缺少 name")
                elif name not in self._compound_library:
                    errors.append(f"动作 {index}: 复合动作不存在: {name}")
            else:
                for message in validate_action_params(action.type, action.parameters):
                    errors.append(f"动作 {index}: {message}")
        return errors

    # ------------------------------------------------------------ actions

    def _on_action_selection_changed(self, _row: int) -> None:
        self._update_operation_bar()

    def _on_step_selection_changed(self, _row: int) -> None:
        self._update_operation_bar()

    def _refresh_actions_list(self) -> None:
        self._refresh_list(self.actions_list, self._actions_buffer)

    def _refresh_list(self, qlist: QListWidget, buffer: list[dict[str, Any]]) -> None:
        """Rebuild a numbered description list from a data buffer."""
        qlist.blockSignals(True)
        qlist.clear()
        for index, data in enumerate(buffer, start=1):
            description = describe_action(str(data.get("type", "")), data)
            item = QListWidgetItem(f"{index}. {description}")
            item.setData(Qt.ItemDataRole.UserRole, index - 1)
            qlist.addItem(item)
        qlist.blockSignals(False)
        self._update_operation_bar()

    def _sync_list_order(self, qlist: QListWidget, buffer: list[dict[str, Any]]) -> None:
        """Reorder ``buffer`` to match the visual order of ``qlist`` after drag-drop."""
        new_order: list[dict[str, Any]] = []
        for i in range(qlist.count()):
            item = qlist.item(i)
            idx = item.data(Qt.ItemDataRole.UserRole) if item else None
            if isinstance(idx, int) and 0 <= idx < len(buffer):
                new_order.append(buffer[idx])
        if len(new_order) != len(buffer):
            return
        buffer[:] = new_order
        qlist.blockSignals(True)
        try:
            for i in range(qlist.count()):
                item = qlist.item(i)
                if item:
                    item.setData(Qt.ItemDataRole.UserRole, i)
        finally:
            qlist.blockSignals(False)

    def _sync_actions_from_list(self) -> None:
        """Reorder _actions_buffer to match the visual order after drag-drop."""
        self._sync_list_order(self.actions_list, self._actions_buffer)

    def _on_actions_rows_moved(self) -> None:
        """Sync buffer after internal drag-drop reorder."""
        self._sync_actions_from_list()
        self._mark_dirty()

    def _add_action(self) -> None:
        self._show_embedded_editor("task_action", index=-1, initial={})

    def _edit_action(self) -> None:
        self._edit_entry(
            self.actions_list, self._actions_buffer, "task_action", "动作"
        )

    def _edit_entry(
        self,
        qlist: QListWidget,
        buffer: list[dict[str, Any]],
        mode: str,
        kind_label: str,
    ) -> None:
        """Open the embedded editor for the selected action/step."""
        row = qlist.currentRow()
        if row < 0 or row >= len(buffer):
            QMessageBox.information(
                self, f"编辑{kind_label}", f"请先选择一个{kind_label}。"
            )
            return
        self._show_embedded_editor(mode, index=row, initial=buffer[row])

    def _duplicate_action(self) -> None:
        self._duplicate_entry(
            self.actions_list, self._actions_buffer, self._refresh_actions_list, "动作"
        )

    def _delete_action(self) -> None:
        self._delete_entry(
            self.actions_list, self._actions_buffer, self._refresh_actions_list
        )

    def _duplicate_entry(
        self,
        qlist: QListWidget,
        buffer: list[dict[str, Any]],
        refresh: Callable[[], None],
        kind_label: str,
    ) -> None:
        """Copy the selected action/step below it."""
        row = qlist.currentRow()
        if row < 0 or row >= len(buffer):
            QMessageBox.information(
                self, f"复制{kind_label}", f"请先选择一个{kind_label}。"
            )
            return
        buffer.insert(row + 1, deep_copy(buffer[row]))
        refresh()
        qlist.setCurrentRow(row + 1)
        self._mark_dirty()

    def _delete_entry(
        self,
        qlist: QListWidget,
        buffer: list[dict[str, Any]],
        refresh: Callable[[], None],
    ) -> None:
        """Remove the selected action/step."""
        row = qlist.currentRow()
        if row < 0 or row >= len(buffer):
            return
        del buffer[row]
        refresh()
        self._mark_dirty()

    def _run_single_action(self) -> None:
        """Run only the selected action."""
        self._run_single_entry(
            self._actions_buffer,
            self.actions_list.currentRow(),
            name_edit=self.task_name_edit,
            kind_label="动作",
            default_name="单动作测试",
        )

    def _run_single_entry(
        self,
        entries: list[dict[str, Any]],
        row: int,
        *,
        name_edit: QLineEdit,
        kind_label: str,
        default_name: str,
    ) -> None:
        """Expand and emit one selected action/step for a debug run."""
        if row < 0 or row >= len(entries):
            QMessageBox.information(
                self, f"运行{kind_label}", f"请先选择一个{kind_label}。"
            )
            return
        entry = entries[row]
        package = self.task_package_edit.text().strip()
        task_name = name_edit.text().strip() or default_name
        expanded, error = expand_action_for_run(
            entry,
            self._compound_library,
            {"qq_group_name": self.settings.get("qq_group_name", "")},
        )
        if error:
            QMessageBox.warning(self, f"无法运行{kind_label}", error)
            return
        self.run_action_requested.emit(
            [{"type": item.type, **item.parameters} for item in expanded],
            package,
            task_name,
        )

    def _view_task_json(self) -> None:
        data = {
            "id": self.task_id_edit.text().strip(),
            "name": self.task_name_edit.text().strip(),
            "package": self.task_package_edit.text().strip(),
            "actions": deep_copy(self._actions_buffer),
        }
        self.embedded_json_viewer.load_data(data)
        self._embedded_previous_panel = self.editor_stack.currentWidget()
        self.editor_stack.setCurrentWidget(self.embedded_json_viewer_panel)

    def _on_dump_tree_clicked(self) -> None:
        self._dump_ui_tree()

    def _on_copy_package_clicked(self) -> None:
        try:
            adb = self._connect_to_mumu()
            package = adb.current_package()
        except Exception as exc:
            self._show_timed_warning("获取包名失败", str(exc))
            return
        if not package:
            self._show_timed_warning("获取包名失败", "未识别到前台应用包名。")
            return
        QGuiApplication.clipboard().setText(package)
        self.feedback_requested.emit(package)

    def _on_view_json_clicked(self) -> None:
        if self.left_tabs.currentIndex() == 0:
            self._view_task_json()
        else:
            self._view_compound_json()

    def _dump_ui_tree(self) -> None:
        if self._ui_tree_thread is not None and self._ui_tree_thread.isRunning():
            return

        thread = QThread(self)
        worker = _UiTreeDumpWorker(self.settings)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._on_ui_tree_dump_succeeded)
        worker.failed.connect(self._on_ui_tree_dump_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_ui_tree_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._ui_tree_thread = thread
        self._ui_tree_worker = worker
        thread.start()

    def _on_ui_tree_dump_succeeded(self, snapshot: UiSnapshot, adb: AdbClient) -> None:
        self._ui_tree_adb = adb
        self.embedded_ui_tree_viewer.load_snapshot(snapshot)
        self._embedded_previous_panel = self.editor_stack.currentWidget()
        self.editor_stack.setCurrentWidget(self.embedded_ui_tree_viewer_panel)

    def _on_ui_tree_dump_failed(self, message: str) -> None:
        self._show_timed_warning("抓取 UI 树失败", message)

    def _on_ui_tree_thread_finished(self) -> None:
        self._ui_tree_thread = None
        self._ui_tree_worker = None

    def _show_timed_warning(self, title: str, message: str) -> None:
        """Show a non-modal notification that disappears after five seconds."""

        if self._task_toast is not None:
            self._task_toast.close()
            self._task_toast = None
        popup = _TaskToast(self, title, message)
        self._task_toast = popup
        popup.show_for_five_seconds()
        QTimer.singleShot(5000, lambda: self._clear_task_toast(popup))

    def _clear_task_toast(self, popup: _TaskToast) -> None:
        if self._task_toast is popup:
            self._task_toast = None

    def set_pointer_location(self, enabled: bool) -> bool:
        """Toggle Android's on-screen pointer coordinate overlay."""

        try:
            adb = self._connect_to_mumu()
            adb.shell(
                "settings",
                "put",
                "system",
                "pointer_location",
                "1" if enabled else "0",
            )
        except Exception as exc:
            title = "显示坐标失败" if enabled else "隐藏坐标失败"
            self._show_timed_warning(title, str(exc))
            return False
        return True

    def _connect_to_mumu(self) -> AdbClient:
        return connect_to_mumu(self.settings)

    def _on_editor_tab_changed(self, index: int) -> None:
        previous_index = self._active_tab_index
        if index != previous_index and not self._resolve_unsaved_changes(
            "切换任务类型", tab_index=previous_index
        ):
            self.left_tabs.blockSignals(True)
            self.left_tabs.setCurrentIndex(previous_index)
            self.left_tabs.blockSignals(False)
            self._update_operation_bar()
            return
        self._active_tab_index = index
        self.editor_stack.setCurrentIndex(index)
        if index == 1 and self.compound_list.currentRow() < 0 and self.compound_list.count():
            self.compound_list.setCurrentRow(0)
        self._update_operation_bar()



# ------------------------------------------------------------ style

    @staticmethod
    def _style_sheet() -> str:
        return (
            "\n"
            "            QWidget { background: transparent; color: #193331; }\n"
            "            QDialog { background: #f2f6f4; color: #193331; }\n"
            "            QFrame#taskManagerLeftPanel {\n"
            "                background: #ffffff;\n"
            "                border: 1px solid #e0e9e6;\n"
            "                border-radius: 0;\n"
            "            }\n"
            "            QFrame#taskManagerEditorPanel {\n"
                "                background: #ffffff;\n"
            "                border: none;\n"
            "                border-radius: 0;\n"
            "            }\n"
            "            QFrame#taskManagerRightPanel {\n"
            "                background: #ffffff;\n"
            "            }\n"
            "            QFrame#taskManagerOperationSeparator {\n"
            "                background: #d9e5e1;\n"
            "                min-height: 30px;\n"
            "                max-height: 30px;\n"
            "            }\n"
            "            QFrame#taskManagerOperationStatus {\n"
            "                background: #edf7f4;\n"
            "                border: 1px solid #d2e7e1;\n"
            "                border-radius: 9px;\n"
            "            }\n"
            "            QLabel#taskManagerOperationLabel {\n"
            "                background: #e1f1ec;\n"
            "                border: 1px solid #b9d9d0;\n"
            "                border-radius: 8px;\n"
            "                color: #176b62;\n"
            "                font-size: 12px;\n"
            "                font-weight: 700;\n"
            "                min-width: 86px;\n"
            "                min-height: 32px;\n"
            "                padding: 0 12px;\n"
            "            }\n"
            "            QLabel#taskManagerDirtyLabel {\n"
                "                color: #a06b1e;\n"
            "                font-size: 11px;\n"
            "                font-weight: 650;\n"
            "                padding: 0 2px;\n"
            "            }\n"
            "            QFrame#taskManagerHLine {\n"
            "                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d9e5e1, stop:0.5 #b9d3ca, stop:1 #d9e5e1);\n"
            "                border: none;\n"
            "            }\n"
            "            QFrame#taskManagerVLine {\n"
            "                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d9e5e1, stop:0.5 #b9d3ca, stop:1 #d9e5e1);\n"
            "                border: none;\n"
            "            }\n"
            "            QListWidget#settingsTaskList {\n"
            "                background: transparent;\n"
            "                border: 0;\n"
            "                outline: 0;\n"
            "                padding: 4px 2px;\n"
            "            }\n"
            "            QListWidget#settingsTaskList::item {\n"
            "                background: #f7faf9;\n"
            "                border: 1px solid #e0e9e6;\n"
            "                border-radius: 8px;\n"
            "                padding: 12px 14px;\n"
            "                color: #2a4243;\n"
            "                margin-bottom: 4px;\n"
            "            }\n"
            "            QListWidget#settingsTaskList::item:hover {\n"
            "                background: #edf7f4;\n"
            "                border-color: #a8d5cc;\n"
            "            }\n"
            "            QListWidget#settingsTaskList::item:selected {\n"
            "                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #dff2ed, stop:1 #d0ebe4);\n"
            "                border: 1px solid #65b3a7;\n"
            "                color: #0f625b;\n"
            "                outline: none;\n"
            "            }\n"
            "            QTabWidget#taskManagerTabs::pane {\n"
            "                border: none;\n"
            "                background: transparent;\n"
            "            }\n"
            "            QTabBar::tab {\n"
            "                background: transparent;\n"
            "                color: #49615f;\n"
            "                padding: 10px 12px;\n"
            "                border-bottom: 3px solid transparent;\n"
            "                font-weight: 650;\n"
            "                min-width: 100px;\n"
            "            }\n"
            "            QTabBar::tab:selected {\n"
            "                color: #0c6e63;\n"
            "                border-bottom: 3px solid #137f73;\n"
            "                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f7faf9, stop:1 #edf7f4);\n"
            "            }\n"
            "            QTabBar::tab:hover {\n"
            "                color: #0c6e63;\n"
            "                background: #f7faf9;\n"
            "            }\n"
            "            QLabel#settingsSectionTitle {\n"
            "                color: #193331;\n"
            "                font-size: 18px;\n"
            "                font-weight: 750;\n"
            "            }\n"
            "            QLabel#settingsCardTitle {\n"
            "                color: #244340;\n"
            "                font-size: 14px;\n"
            "                font-weight: 700;\n"
            "                padding: 4px 0;\n"
            "            }\n"
            + _s.OCR_FEEDBACK_QSS
            + "            QFormLayout QLabel { color: #49615f; font-size: 13px; font-weight: 500; }\n"
            + _s.MESSAGE_BOX_QSS
            + _s.SCROLLBAR_QSS
            + _s.COMMON_CONTROLS_QSS
            + "        "
        )
