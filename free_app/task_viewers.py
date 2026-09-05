"""任务管理页的可嵌入查看器控件。

JsonViewerWidget / RunViewerWidget / UiTreeDumpWidget 都是挂在任务管理页
右侧栈中的查看面板：JSON 预览、单动作试运行输出、UI 树抓取与点击插入。
UI 树的后台抓取 worker 与搜索过滤助手也放在这里，保持"查看器"内聚。
"""

from __future__ import annotations

import json
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import styles as _s
from .logging_utils import format_log_line
from .message_box import QMessageBox
from .models import RunResult, RunStatus
from .mumu import connect_to_mumu
from .settings_dialog import SettingsComboBox
from .ui_automation import UiSnapshot, text_matches_exact, text_matches_fuzzy


def _ui_tree_search_matches(
    label: str, resource_id: str, needle: str, mode: str
) -> bool:
    """按精确或模糊（``%`` / ``_`` / 子串）方式匹配行的 ``label`` 或 ``resource_id``；空搜索词匹配全部。"""

    needle = needle.strip()
    if not needle:
        return True
    if mode == "exact":
        return text_matches_exact(label, needle) or resource_id.strip() == needle
    return text_matches_fuzzy(label, needle) or needle.lower() in resource_id.lower()


class UiTreeDumpWorker(QObject):
    """在 GUI 线程之外抓取当前 UI 树（连接设备、获取 XML、解析快照）。"""

    succeeded = Signal(object, object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, settings: dict[str, Any]) -> None:
        super().__init__()
        self._settings = dict(settings)
        self._stop_requested = Event()

    def request_stop(self) -> None:
        self._stop_requested.set()

    def run(self) -> None:
        try:
            if self._stop_requested.is_set():
                return
            adb = connect_to_mumu(self._settings)
            if self._stop_requested.is_set():
                return
            xml = adb.dump_ui()
            snapshot = UiSnapshot.from_xml(xml)
        except Exception as exc:
            if not self._stop_requested.is_set():
                self.failed.emit(str(exc))
        else:
            self.succeeded.emit(snapshot, adb)
        finally:
            # finished 驱动 thread.quit：此前 run 从不触发它，线程事件循环
            # 永不退出，应用退出时 QThread 仍在运行（destroyed-while-running）。
            self.finished.emit()


class JsonViewerWidget(QWidget):
    """任务或复合任务 JSON 数据的只读内嵌查看器。"""

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
        self.editor.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))

    @staticmethod
    def _style_sheet() -> str:
        return (
            "\n"
            + _s.PANEL_BASE_QSS
            + _s.PANEL_CONTENT_QSS
            + _s.CARD_TITLE_QSS
            + _s.OCR_FEEDBACK_QSS
            + _s.MESSAGE_BOX_QSS
            + _s.COMMON_CONTROLS_QSS
            + "        "
        )


class RunViewerWidget(QWidget):
    """单动作调试运行用的内嵌输出面板。"""

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
        """重置面板并把运行标记为进行中。"""
        self.status_label.setText("运行中")
        self._paint_status("#0c6e63")
        self.progress_label.setText(f"{task_name or '单动作测试'} · 准备连接设备")
        self.log_edit.clear()
        self.stop_button.setEnabled(True)

    def append_log(self, message: str) -> None:
        """追加一行带时间戳的日志，并保持视图滚动到底部。"""
        self.log_edit.appendPlainText(format_log_line(message))
        scroll_bar = self.log_edit.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    def set_progress(self, index: int, total: int, description: str) -> None:
        self.progress_label.setText(f"当前动作 {index} / {total} · {description}")

    def finish_run(self, result: RunResult) -> None:
        """在内嵌面板中显示最终状态与结果摘要。"""
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
        self.progress_label.setText(
            f"当前动作 {result.completed_steps} / {result.total_steps}"
        )
        error = f"，错误: {result.error}" if result.error else ""
        self.append_log(
            f"运行结束: status={result.status.value}, "
            f"completed={result.completed_steps}/{result.total_steps}{error}"
        )

    def abort_run(self, message: str) -> None:
        """准备失败后把面板标记为未运行。"""
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
            + _s.PANEL_BASE_QSS
            + _s.PANEL_CONTENT_QSS
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
            "            }\n" + _s.MESSAGE_BOX_QSS + _s.COMMON_CONTROLS_QSS + "        "
        )


class UiTreeDumpWidget(QWidget):
    """调试用的内嵌 UI 树查看器；支持从选中节点插入点击动作。"""

    action_inserted = Signal(dict)
    try_click_requested = Signal(int, int, str)

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
        self.search_edit.textChanged.connect(self._schedule_filter)
        self.search_edit.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        search_row.addWidget(self.search_edit, 1)
        self.search_mode_combo = SettingsComboBox()
        self.search_mode_combo.addItem("模糊", "fuzzy")
        self.search_mode_combo.addItem("精确", "exact")
        self.search_mode_combo.setCurrentIndex(self.search_mode_combo.findData("fuzzy"))
        self.search_mode_combo.currentIndexChanged.connect(self._schedule_filter)
        self.search_mode_combo.setFixedWidth(88)
        search_row.addWidget(self.search_mode_combo)
        # 输入防抖：逐键过滤会对数百个节点逐个 setHidden（每个都触发重排），
        # 停顿 200ms 后才真正执行。
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._apply_filter)
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
        hint = QLabel(
            "选中节点后可先尝试点击，或双击节点生成 click 动作插入任务动作列表。"
        )
        hint.setObjectName("settingsOcrFeedback")
        layout.addWidget(hint)

    @staticmethod
    def _style_sheet() -> str:
        return (
            "\n"
            + _s.PANEL_BASE_QSS
            + _s.PANEL_CONTENT_QSS
            + _s.CARD_TITLE_QSS
            + _s.OCR_FEEDBACK_QSS
            + "            QPushButton#settingsTryButton {\n"
            "                color: #ffffff;\n"
            "                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2b9b8b, stop:1 #137f73);\n"
            "                border: none;\n"
            "                font-weight: 700;\n"
            "            }\n"
            "            QPushButton#settingsTryButton:hover {\n"
            "                background: " + _s.GREEN_VGRAD_HOVER + ";\n"
            "            }\n" + _s.MESSAGE_BOX_QSS + _s.COMMON_CONTROLS_QSS + "        "
        )

    def load_snapshot(self, snapshot: UiSnapshot) -> None:
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

    def _schedule_filter(self, *_args: object) -> None:
        self._search_timer.start()

    def _apply_filter(self) -> None:
        """隐藏与当前搜索词不匹配的 UI 树节点。"""
        needle = self.search_edit.text().strip()
        mode = str(self.search_mode_combo.currentData() or "fuzzy")
        for item in self._all_items:
            matched = not needle or _ui_tree_search_matches(
                item.text(0), item.text(1), needle, mode
            )
            item.setHidden(not matched)

    def _insert_click(self) -> None:
        item = self.tree.currentItem()
        if item is None or self._snapshot is None:
            return
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if (
            not isinstance(index, int)
            or index < 0
            or index >= len(self._snapshot.nodes)
        ):
            return
        node = self._snapshot.nodes[index]
        mode = self.insert_mode_combo.currentData() or "text"
        if mode == "resource_id":
            if not node.resource_id:
                QMessageBox.information(
                    self, "无法插入", "该节点没有 resource-id，请选择其他插入方式。"
                )
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
                QMessageBox.information(
                    self, "无法插入", "该节点没有文本，请选择其他插入方式。"
                )
                return
            action = {
                "type": "click",
                "locate": "ui",
                "target": "text",
                "texts": [node.label],
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
        if (
            not isinstance(index, int)
            or index < 0
            or index >= len(self._snapshot.nodes)
        ):
            return
        node = self._snapshot.nodes[index]
        if not node.bounds:
            QMessageBox.information(self, "无法尝试点击", "该节点没有可点击坐标。")
            return
        x, y = node.bounds.center
        self.try_click_requested.emit(x, y, node.label)
