"""任务管理页：编辑任务、复合动作库与 JSON 预览。"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import styles as _s
from .action_editor_dialogs import (
    ActionEditorWidget,
    ActionListEditorWidget,
)
from .action_schema import COMPOUND_TYPE, describe_action, validate_action_params
from .adb import AdbClient
from .background_task import BackgroundTaskOwner
from .config import (
    expand_action_for_run,
    load_action_library,
    load_json,
    load_settings,
    load_task_directory_raw,
    update_settings,
)
from .helpers import deep_copy, write_json_file
from .message_box import QMessageBox
from .models import Action, RunResult, TaskDefinition
from .mumu import connect_to_mumu
from .settings_dialog import SettingsComboBox, confirm_dialog
from .task_viewers import (
    JsonViewerWidget,
    RunViewerWidget,
    UiTreeDumpWidget,
    UiTreeDumpWorker,
)
from .trash import TrashError, remove_path
from .ui_automation import UiSnapshot


@dataclass
class _EmbeddedNavigator:
    """内嵌编辑器与内嵌查看器的导航状态。

    以前散落在 TaskManagerWidget 的多个属性上，任何一处忘记复位都会让
    提交/返回落到错误的面板。这里统一维护：
    - ``mode`` / ``index`` / ``return_panel`` / ``original``：一次内嵌动作
      或步骤编辑会话；
    - ``branch_stack``：分支步骤编辑的父级上下文栈；
    - ``previous_panel``：内嵌查看器（JSON / UI 树）的返回面板。
    """

    mode: str | None = None  # 取值：task_action / compound_step / branch_step
    index: int = -1
    return_panel: QWidget | None = None
    original: dict[str, Any] | None = None
    previous_panel: QWidget | None = None
    branch_stack: list[dict[str, Any]] = field(default_factory=list)

    def close_editor(self) -> QWidget | None:
        """结束当前编辑会话，返回应回到的面板（None 时由调用方兜底）。"""

        return_panel = self.return_panel
        self.mode = None
        self.index = -1
        self.return_panel = None
        self.original = None
        return return_panel


@dataclass
class _EntryPanelState:
    """一个名称列表页（任务 / 复合任务）的选中与草稿状态。"""

    selected: str | None = None
    creating: bool = False
    unsaved: set[str] = field(default_factory=set)


@dataclass
class _EntryListPanel:
    """名称列表页的共享挂点。

    任务列表与复合任务列表的刷新、选中切换、重复点击聚焦和"新建"流程
    完全同构，差异只在控件、数据源与编辑器回调；用这一个规格对象驱动
    共享实现，避免两套平行方法各自演化。
    """

    list_widget: QListWidget
    stack_index: int  # 选中后编辑器所在的 editor_stack 页
    steps_list: QListWidget  # 点击已选中行时清空选择的步骤列表
    state: _EntryPanelState
    entries: Callable[[], dict[str, dict[str, Any]]]
    load_editor: Callable[[dict[str, Any]], None]
    clear_editor: Callable[[], None]
    label_for: Callable[[str, dict[str, Any]], str]
    switch_label: str  # 未保存切换确认文案，如 "切换任务"
    new_label: str  # "新建任务"
    on_empty: Callable[[], None]  # 列表为空且无选中时的兜底


class _TaskToast(QFrame):
    """任务管理页短暂错误提示用的非模态通知。"""

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


class TaskManagerWidget(BackgroundTaskOwner, QWidget):
    """完整的任务管理面板：任务、动作编辑器与复合动作库。"""

    # _commit_embedded_editors 需要逐层退出的内嵌步骤/动作编辑器最大嵌套深度
    # （任务 -> if 分支步骤 -> 动作 -> 嵌套步骤）。
    _MAX_EMBEDDED_EDITOR_DEPTH = 4

    tasks_changed = Signal()
    feedback_requested = Signal(str)
    run_action_requested = Signal(list, str, str)  # 动作列表、包名、任务名
    run_stop_requested = Signal()
    # 指针坐标开关失败（后台执行）时通知主窗口回退按钮状态。
    pointer_location_failed = Signal(bool)

    def __init__(self, settings_path: Path, base_directory: Path | None = None):
        super().__init__()
        self.settings_path = settings_path
        self.base_directory = base_directory or settings_path.parent
        self.settings = load_settings(settings_path)
        self._init_background_tasks()
        self._ui_tree_adb: AdbClient | None = None
        self._ui_tree_thread: QThread | None = None
        self._ui_tree_worker: UiTreeDumpWorker | None = None
        self._tasks: dict[str, dict[str, Any]] = {}
        self._compound_library: dict[str, dict[str, Any]] = {}
        self._task_state = _EntryPanelState()
        self._compound_state = _EntryPanelState()
        self._actions_buffer: list[dict[str, Any]] = []
        self._steps_buffer: list[dict[str, Any]] = []
        self._compound_description_buffer = ""
        self._compound_description_present = False
        self._embedded = _EmbeddedNavigator()
        self._task_toast: _TaskToast | None = None
        self._dirty = False
        self._suspend_dirty = False
        self._active_tab_index = 0
        self._build_ui()
        self._task_panel = _EntryListPanel(
            list_widget=self.task_list,
            stack_index=0,
            steps_list=self.actions_list,
            state=self._task_state,
            entries=lambda: self._tasks,
            load_editor=self._load_task_editor,
            clear_editor=self._clear_editor,
            label_for=lambda key, data: f"{data.get('name', '')}（{key}）",
            switch_label="切换任务",
            new_label="新建任务",
            on_empty=self._clear_editor,
        )
        self._compound_panel = _EntryListPanel(
            list_widget=self.compound_list,
            stack_index=1,
            steps_list=self.compound_steps_list,
            state=self._compound_state,
            entries=lambda: self._compound_library,
            load_editor=self._load_compound_editor,
            clear_editor=self._clear_compound_editor,
            label_for=lambda key, _data: str(key),
            switch_label="切换复合任务",
            new_label="新建复合任务",
            on_empty=self._clear_compound_selection,
        )
        self.setStyleSheet(self._style_sheet())
        self.reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        # 让编辑器主体与页面边缘齐平，与主页表面一致；
        # 头部自管水平边距。
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(0)

        h_line = QFrame()
        h_line.setObjectName("taskManagerHLine")
        h_line.setFixedHeight(2)
        root.addWidget(h_line)

        body = QHBoxLayout()
        body.setSpacing(0)

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
        self.task_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
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
        self.compound_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.compound_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.compound_list.currentRowChanged.connect(self._on_compound_selected)
        self.compound_list.itemClicked.connect(self._on_compound_item_clicked)
        self.compound_list.itemDoubleClicked.connect(
            lambda _item: self._edit_compound()
        )
        compound_tab_layout.addWidget(self.compound_list, 1)
        self.left_tabs.addTab(compound_tab, "复合任务")

        self.left_tabs.currentChanged.connect(self._on_editor_tab_changed)
        left_layout.addWidget(self.left_tabs, 1)

        # 隐藏下拉框，承载删除/保存路径使用的清理方式。
        self.cleanup_mode_combo = SettingsComboBox()
        self.cleanup_mode_combo.addItem("删除至回收站", "recycle")
        self.cleanup_mode_combo.addItem("永久删除", "permanent")
        self.cleanup_mode_combo.currentIndexChanged.connect(
            self._on_cleanup_mode_changed
        )
        self.cleanup_mode_combo.hide()

        left_panel.setFixedWidth(280)
        body.addWidget(left_panel)

        v_line = QFrame()
        v_line.setObjectName("taskManagerVLine")
        v_line.setFixedWidth(2)
        body.addWidget(v_line)

        self.editor_stack = QStackedWidget()
        self.editor_stack.setObjectName("taskManagerEditors")

        editor_panel = QFrame()
        editor_panel.setObjectName("taskManagerEditorPanel")
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(14, 14, 14, 14)
        editor_layout.setSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        self.task_id_edit = QLineEdit()
        self.task_id_edit.setPlaceholderText("例如：bilibili_exp")
        self._disable_context_menu(self.task_id_edit)
        self.task_name_edit = QLineEdit()
        self.task_name_edit.setPlaceholderText("任务显示名称")
        self._disable_context_menu(self.task_name_edit)
        self.task_package_edit = QLineEdit()
        self.task_package_edit.setPlaceholderText("例如：tv.danmaku.bili")
        self._disable_context_menu(self.task_package_edit)
        form.addRow("id", self.task_id_edit)
        form.addRow("名称", self.task_name_edit)
        form.addRow("包名", self.task_package_edit)
        for editor_field in (
            self.task_id_edit,
            self.task_name_edit,
            self.task_package_edit,
        ):
            editor_field.textChanged.connect(self._mark_dirty)
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

        compound_editor_panel = QFrame()
        compound_editor_panel.setObjectName("taskManagerEditorPanel")
        compound_editor_layout = QVBoxLayout(compound_editor_panel)
        compound_editor_layout.setContentsMargins(14, 14, 14, 14)
        compound_editor_layout.setSpacing(12)

        compound_form = QFormLayout()
        compound_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        compound_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        compound_form.setHorizontalSpacing(14)
        compound_form.setVerticalSpacing(10)
        self.compound_name_edit = QLineEdit()
        self.compound_name_edit.setPlaceholderText("例如：share_group")
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
        self.compound_steps_list.model().rowsMoved.connect(
            self._on_compound_steps_moved
        )
        self.compound_steps_list.currentRowChanged.connect(
            self._on_step_selection_changed
        )
        self.compound_steps_list.itemDoubleClicked.connect(
            lambda _item: self._edit_step()
        )
        compound_editor_layout.addWidget(self.compound_steps_list, 1)
        self.editor_stack.addWidget(compound_editor_panel)

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
        self.embedded_action_editor.nested_steps_edit_requested.connect(
            self._open_branch_steps_editor
        )
        embedded_layout.addWidget(self.embedded_action_editor)
        self.editor_stack.addWidget(self.embedded_action_editor_panel)

        self.embedded_branch_steps_editor_panel = QFrame()
        self.embedded_branch_steps_editor_panel.setObjectName("taskManagerEditorPanel")
        branch_steps_layout = QVBoxLayout(self.embedded_branch_steps_editor_panel)
        branch_steps_layout.setContentsMargins(14, 14, 14, 14)
        branch_steps_layout.setSpacing(12)
        self.embedded_branch_steps_editor = ActionListEditorWidget(
            self.embedded_branch_steps_editor_panel
        )
        self.embedded_branch_steps_editor.add_step_requested.connect(
            self._add_branch_step
        )
        self.embedded_branch_steps_editor.edit_step_requested.connect(
            self._edit_branch_step
        )
        branch_steps_layout.addWidget(self.embedded_branch_steps_editor)
        self.editor_stack.addWidget(self.embedded_branch_steps_editor_panel)

        self.embedded_json_viewer_panel = QFrame()
        self.embedded_json_viewer_panel.setObjectName("taskManagerEditorPanel")
        json_viewer_layout = QVBoxLayout(self.embedded_json_viewer_panel)
        json_viewer_layout.setContentsMargins(0, 0, 0, 0)
        json_viewer_layout.setSpacing(0)
        self.embedded_json_viewer = JsonViewerWidget(
            self.embedded_json_viewer_panel, data={}
        )
        json_viewer_layout.addWidget(self.embedded_json_viewer)
        self.editor_stack.addWidget(self.embedded_json_viewer_panel)

        self.embedded_ui_tree_viewer_panel = QFrame()
        self.embedded_ui_tree_viewer_panel.setObjectName("taskManagerEditorPanel")
        ui_tree_viewer_layout = QVBoxLayout(self.embedded_ui_tree_viewer_panel)
        ui_tree_viewer_layout.setContentsMargins(0, 0, 0, 0)
        ui_tree_viewer_layout.setSpacing(0)
        self.embedded_ui_tree_viewer = UiTreeDumpWidget(
            self.embedded_ui_tree_viewer_panel
        )
        self.embedded_ui_tree_viewer.action_inserted.connect(
            self._on_embedded_ui_tree_action_inserted
        )
        self.embedded_ui_tree_viewer.try_click_requested.connect(
            self._on_embedded_ui_tree_try_click_requested
        )
        ui_tree_viewer_layout.addWidget(self.embedded_ui_tree_viewer)
        self.editor_stack.addWidget(self.embedded_ui_tree_viewer_panel)

        self.embedded_run_viewer_panel = QFrame()
        self.embedded_run_viewer_panel.setObjectName("taskManagerEditorPanel")
        run_viewer_layout = QVBoxLayout(self.embedded_run_viewer_panel)
        run_viewer_layout.setContentsMargins(0, 0, 0, 0)
        run_viewer_layout.setSpacing(0)
        self.embedded_run_viewer = RunViewerWidget(self.embedded_run_viewer_panel)
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
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

    def _mark_dirty(self, *_args: Any) -> None:
        """将当前编辑器标记为已修改；重载/载入数据期间（``_suspend_dirty``）不生效。"""

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
                if self._task_state.creating or self.actions_list.currentRow() >= 0
                else "task"
            )
        return (
            "step"
            if self._compound_state.creating
            or self.compound_steps_list.currentRow() >= 0
            else "compound"
        )

    def _embedded_editor_has_changes(self) -> bool:
        current = self.editor_stack.currentWidget()
        if current is self.embedded_action_editor_panel:
            if self._embedded.original is None:
                return False
            return (
                self.embedded_action_editor.snapshot_data() != self._embedded.original
            )
        if (
            current is self.embedded_branch_steps_editor_panel
            and self._embedded.branch_stack
        ):
            context = self._embedded.branch_stack[-1]
            return self.embedded_branch_steps_editor.get_steps() != context.get(
                "original_steps", []
            )
        return False

    def _update_operation_bar(self) -> None:
        """按当前活动对象刷新底部唯一的操作栏。"""

        context = self._current_operation_context()
        base_context = context in {"task", "compound"}
        action_context = context in {"action", "step", "embedded_branch"}
        editing_context = context in {"embedded_action", "embedded_branch"}
        has_selection = (
            self._task_state.selected is not None
            if context == "task"
            else self._compound_state.selected is not None
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
            base_context
            or editing_context
            or self._dirty
            or self._embedded_editor_has_changes()
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
        if (
            current
            in (
                self.embedded_action_editor_panel,
                self.embedded_branch_steps_editor_panel,
            )
            and not self._commit_embedded_editors()
        ):
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
        """在列表点击切换上下文前，先逐层退回基础编辑器；用户取消则返回 False。"""

        while self.editor_stack.currentWidget() in (
            self.embedded_action_editor_panel,
            self.embedded_branch_steps_editor_panel,
        ):
            if not self._leave_embedded_editor():
                return False
        return True

    def _ask_unsaved_changes(self, _operation: str) -> str:
        """询问是否在继续之前放弃当前修改。"""

        message_box = QMessageBox(self)
        message_box.setWindowTitle("未保存的修改")
        message_box.setText("当前内容尚未保存，确定放弃更改吗？")
        # Qt 在 Windows 上按角色排列消息框按钮。使用下面的角色，
        # 使可见顺序明确为：取消（左）、确认（右）。
        cancel_button = message_box.addButton("取消", QMessageBox.ButtonRole.AcceptRole)
        confirm_button = message_box.addButton(
            "确认", QMessageBox.ButtonRole.DestructiveRole
        )
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
        """在切换上下文前处理外层编辑器的未保存修改；用户取消则返回 False。"""

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
            task_id = self._task_state.selected
            if task_id in self._task_state.unsaved:
                self._tasks.pop(str(task_id), None)
                self._task_state.unsaved.discard(str(task_id))
                self._refresh_task_list()
            elif task_id and task_id in self._tasks:
                self._load_task_editor(self._tasks[task_id])
            else:
                self._clear_editor()
        else:
            name = self._compound_state.selected
            if name in self._compound_state.unsaved:
                self._compound_state.unsaved.discard(str(name))
                self._clear_compound_editor()
                self._refresh_compound_list()
            elif name and name in self._compound_library:
                self._load_compound_editor(self._compound_library[name])
            else:
                self._clear_compound_editor()
        self._update_operation_bar()

    # 数据

    def reload(self) -> None:
        """重新读取设置、任务文件与复合动作库。

        打开管理页不得改动磁盘上的任何内容：设置由设置页/清理方式下拉框
        写入，``tasks_changed`` 信号也只由真正修改任务文件的操作发出。
        """
        self.settings = load_settings(self.settings_path)
        tasks_directory = self.base_directory / "config" / "tasks"
        loaded: dict[str, dict[str, Any]] = {}
        try:
            # 原始 JSON 与校验结果一次读出，编辑器不再二次加载任务文件。
            _tasks, _errors, loaded = load_task_directory_raw(
                tasks_directory,
                variables={"qq_group_name": self.settings.get("qq_group_name", "")},
            )
        except (FileNotFoundError, OSError, ValueError):
            pass
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
        # 复用与任务加载相同的库解析（单份实现、一致的损坏报告）。
        self._compound_library = load_action_library(actions_directory)
        self._task_state.unsaved.clear()
        self._compound_state.unsaved.clear()
        self._task_state.creating = False
        self._compound_state.creating = False
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
        self._embedded.close_editor()
        self._embedded.branch_stack.clear()
        self._suspend_dirty = False
        self._update_operation_bar()

    def _refresh_task_list(self, select_id: str | None = None) -> None:
        self._refresh_entry_list(self._task_panel, select_id)

    def _on_task_selected(self, row: int) -> None:
        self._on_entry_selected(self._task_panel, row)

    def _on_task_item_clicked(self, item: QListWidgetItem) -> None:
        self._on_entry_item_clicked(self._task_panel, item)

    def _restore_task_selection(self, task_id: str | None) -> None:
        self._restore_entry_selection(self._task_panel, task_id)

    def _refresh_entry_list(
        self,
        panel: _EntryListPanel,
        select_key: str | None = None,
    ) -> None:
        """重建名称列表；``select_key`` 命中则选中该行，否则选第一行。"""

        list_widget = panel.list_widget
        list_widget.blockSignals(True)
        list_widget.clear()
        for key, data in panel.entries().items():
            item = QListWidgetItem(panel.label_for(key, data))
            item.setData(Qt.ItemDataRole.UserRole, key)
            list_widget.addItem(item)
        list_widget.blockSignals(False)
        if select_key is not None and self._select_list_row(list_widget, select_key):
            return
        if list_widget.count():
            list_widget.setCurrentRow(0)
        else:
            panel.on_empty()
        self._update_operation_bar()

    @staticmethod
    def _select_list_row(list_widget: QListWidget, key: str) -> bool:
        """选中 UserRole 数据等于 ``key`` 的行；返回是否命中。"""

        for index in range(list_widget.count()):
            if list_widget.item(index).data(Qt.ItemDataRole.UserRole) == key:
                list_widget.setCurrentRow(index)
                return True
        return False

    def _on_entry_selected(self, panel: _EntryListPanel, row: int) -> None:
        item = panel.list_widget.item(row)
        raw = item.data(Qt.ItemDataRole.UserRole) if item else None
        next_key = str(raw) if raw else None
        if next_key == panel.state.selected:
            self._update_operation_bar()
            return
        previous = panel.state.selected
        if not self._resolve_unsaved_changes(panel.switch_label):
            self._restore_entry_selection(panel, previous)
            return
        panel.state.selected = next_key
        data = panel.entries().get(next_key or "")
        if data is None:
            panel.clear_editor()
            return
        panel.load_editor(data)
        self.editor_stack.setCurrentWidget(self.editor_stack.widget(panel.stack_index))

    def _on_entry_item_clicked(
        self, panel: _EntryListPanel, item: QListWidgetItem
    ) -> None:
        """点击已选中的行时聚焦该对象本身。"""

        if str(item.data(Qt.ItemDataRole.UserRole) or "") != str(
            panel.state.selected or ""
        ):
            return
        if not self._leave_all_embedded_editors():
            return
        panel.steps_list.setCurrentRow(-1)
        self._embedded.previous_panel = None
        self.editor_stack.setCurrentWidget(self.editor_stack.widget(panel.stack_index))
        self._update_operation_bar()

    def _restore_entry_selection(self, panel: _EntryListPanel, key: str | None) -> None:
        self._restore_list_selection(panel.list_widget, key, self._update_operation_bar)

    @staticmethod
    def _restore_list_selection(
        list_widget: QListWidget,
        key: str | None,
        update_bar: Callable[[], None],
    ) -> None:
        """选中 UserRole 数据等于 ``key`` 的行；``key`` 为 None 时清除选中。"""
        list_widget.blockSignals(True)
        try:
            if key is None:
                list_widget.setCurrentRow(-1)
            else:
                for index in range(list_widget.count()):
                    if list_widget.item(index).data(Qt.ItemDataRole.UserRole) == key:
                        list_widget.setCurrentRow(index)
                        break
        finally:
            list_widget.blockSignals(False)
        update_bar()

    def _load_task_editor(self, data: dict[str, Any]) -> None:
        self._task_state.creating = False
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
        self._task_state.creating = False
        self._suspend_dirty = True
        self._task_state.selected = None
        self._actions_buffer = []
        self.task_id_edit.clear()
        self.task_name_edit.clear()
        self.task_package_edit.clear()
        self._refresh_actions_list()
        self._suspend_dirty = False
        self._dirty = False
        self._update_operation_bar()

    # 任务

    def _new_task(self) -> None:
        self._begin_create_entry(self._task_panel)

    def _begin_create_entry(self, panel: _EntryListPanel) -> None:
        if not self._resolve_unsaved_changes(panel.new_label):
            return
        panel.list_widget.setCurrentRow(-1)
        panel.clear_editor()
        panel.state.creating = True
        self._set_dirty(False)

    def _duplicate_task(self) -> None:
        if not self._resolve_unsaved_changes("复制任务"):
            return
        source_id = self._task_state.selected
        if not source_id or source_id not in self._tasks:
            QMessageBox.information(self, "复制任务", "请先选择一个任务。")
            return
        data = deep_copy(self._tasks[source_id])
        candidate = self._unique_copy_name(source_id, self._tasks)
        data["id"] = candidate
        self._tasks[candidate] = data
        self._task_state.unsaved.add(candidate)
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
        task_id = self._task_state.selected
        if not task_id or task_id not in self._tasks:
            QMessageBox.information(self, "删除任务", "请先选择一个任务。")
            return
        if not self._delete_file(
            self._task_path_for_id(task_id), "删除任务", "任务", task_id
        ):
            return
        self._tasks.pop(task_id, None)
        self._task_state.unsaved.discard(task_id)
        task_order = self.settings.get("task_order")
        if not isinstance(task_order, list):
            task_order = []
        try:
            self.settings = update_settings(
                self.settings_path,
                {"task_order": [item for item in task_order if item != task_id]},
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self, "顺序保存失败", f"任务已删除，但任务顺序未持久化：{exc}"
            )
        self._dirty = False
        self._refresh_task_list()
        self.tasks_changed.emit()
        self._update_operation_bar()

    def _delete_file(
        self, path: Path | None, title: str, kind_label: str, name: str
    ) -> bool:
        """按当前清理方式确认并删除 ``path``。

        ``path`` 为 None 或不存在时删除是空操作，但仍视为成功；用户取消或
        删除失败时返回 False，调用方可据此中止后续删除流程。
        """
        mode = str(self.cleanup_mode_combo.currentData() or "recycle")
        mode_label = "永久删除" if mode == "permanent" else "删除至回收站"
        if not confirm_dialog(self, title, f"确定{mode_label}{kind_label} {name}？"):
            return False
        if path is not None and path.exists():
            try:
                remove_path(path, mode)
            except (OSError, TrashError) as exc:
                QMessageBox.warning(self, "删除失败", str(exc))
                return False
        return True

    def _on_cleanup_mode_changed(self, _index: int) -> None:
        mode = str(self.cleanup_mode_combo.currentData() or "recycle")
        self.settings["cleanup_mode"] = mode
        try:
            self.settings = update_settings(self.settings_path, {"cleanup_mode": mode})
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    # 复合动作库

    def _refresh_compound_list(self, select_name: str | None = None) -> None:
        self._refresh_entry_list(self._compound_panel, select_name)

    def _on_compound_selected(self, row: int) -> None:
        self._on_entry_selected(self._compound_panel, row)

    def _on_compound_item_clicked(self, item: QListWidgetItem) -> None:
        self._on_entry_item_clicked(self._compound_panel, item)

    def _restore_compound_selection(self, name: str | None) -> None:
        self._restore_entry_selection(self._compound_panel, name)

    def _clear_compound_selection(self) -> None:
        self._compound_state.selected = None

    def _new_compound(self) -> None:
        self._begin_create_entry(self._compound_panel)

    @staticmethod
    def _unique_copy_name(source: str, existing: dict[str, Any]) -> str:
        """为复制出的对象生成不冲突的 ``xxx_copy`` 名称。"""

        candidate = f"{source}_copy"
        while candidate in existing:
            candidate = f"{candidate}_copy"
        return candidate

    def _duplicate_compound(self) -> None:
        if not self._resolve_unsaved_changes("复制复合任务"):
            return
        source_name = self._compound_state.selected
        if not source_name or source_name not in self._compound_library:
            QMessageBox.information(self, "复制复合任务", "请先选择一个复合任务。")
            return
        data = deep_copy(self._compound_library[source_name])
        candidate = self._unique_copy_name(source_name, self._compound_library)
        data["name"] = candidate
        self.compound_list.setCurrentRow(-1)
        self._compound_state.selected = None
        self._load_compound_editor(data)
        self._compound_state.unsaved.add(candidate)
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
        self._compound_state.unsaved.discard(name)
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

    def _rollback_rename(
        self,
        old_path: Path,
        new_path: Path,
        mode: str,
        restore: Callable[[], None],
    ) -> bool:
        """删除重命名前的旧文件 ``old_path``；失败时回滚删除 ``new_path`` 并执行 ``restore``。

        删除失败且调用方必须中止重命名流程时返回 True（警告框已弹出）。
        """

        try:
            remove_path(old_path, mode)
            return False
        except (OSError, TrashError) as exc:
            rollback_error: Exception | None = None
            try:
                remove_path(new_path, mode)
            except (OSError, TrashError) as rollback_exc:
                rollback_error = rollback_exc
            restore()
            if rollback_error is None:
                message = f"重命名失败，已回滚：{exc}"
            else:
                message = (
                    f"重命名失败，回滚删除新文件也失败：{exc}；{rollback_error}，"
                    "新旧两个文件可能都存在。"
                )
            QMessageBox.warning(self, "保存失败", message)
            return True

    def _save_compound(
        self, data: dict[str, Any], previous_name: str | None = None
    ) -> bool:
        name = str(data.get("name", "")).strip()
        steps = data.get("steps")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
            QMessageBox.warning(
                self,
                "无法保存",
                "复合任务名只能包含字母、数字、下划线和短横线，且以字母或数字开头。",
            )
            return False
        if not isinstance(steps, list) or not steps:
            QMessageBox.warning(self, "无法保存", "复合任务 steps 必须是非空列表。")
            return False
        action_path = self.base_directory / "config" / "actions" / f"{name}.json"
        if previous_name != name and (
            name in self._compound_library or action_path.exists()
        ):
            QMessageBox.warning(
                self, "无法保存", f"复合任务名已存在：{name}，请改名后重试。"
            )
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
                if task_path is None or task_id in self._task_state.unsaved:
                    continue
                write_json_file(task_path, updated)
                written_references.append((task_path, original))
            write_json_file(action_path, data)
        except OSError as exc:
            for task_path, original in written_references:
                try:
                    write_json_file(task_path, original)
                except OSError:
                    pass
            QMessageBox.warning(self, "无法保存", str(exc))
            return False
        if old_path is not None and old_path.exists():
            mode = str(self.cleanup_mode_combo.currentData() or "recycle")

            def _restore_compound() -> None:
                if old_data is not None and previous_name is not None:
                    self._compound_library[previous_name] = old_data
                for task_path, original in written_references:
                    try:
                        write_json_file(task_path, original)
                    except OSError:
                        pass
                self._refresh_compound_list()

            if self._rollback_rename(old_path, action_path, mode, _restore_compound):
                return False
        if previous_name and previous_name != name:
            self._compound_library.pop(previous_name, None)
            self._compound_state.unsaved.discard(previous_name)
        for task_id, _original, updated, _task_path in reference_updates:
            self._tasks[task_id] = updated
        self._compound_state.unsaved.discard(name)
        self._compound_library[name] = data
        self._compound_state.selected = name
        self._compound_state.creating = False
        self._dirty = False
        self._refresh_compound_list(select_name=name)
        self.tasks_changed.emit()
        self._update_operation_bar()
        return True

    def _save_compound_from_editor(self) -> bool:
        data = {
            "name": self.compound_name_edit.text().strip(),
            "steps": deep_copy(self._steps_buffer),
        }
        if self._compound_description_present or self._compound_description_buffer:
            data["description"] = self._compound_description_buffer
        return self._save_compound(data, previous_name=self._compound_state.selected)

    def _view_compound_json(self) -> None:
        data = {
            "name": self.compound_name_edit.text().strip(),
            "steps": deep_copy(self._steps_buffer),
        }
        self.embedded_json_viewer.load_data(data)
        self._embedded.previous_panel = self.editor_stack.currentWidget()
        self.editor_stack.setCurrentWidget(self.embedded_json_viewer_panel)

    def _load_compound_editor(self, data: dict[str, Any]) -> None:
        self._compound_state.creating = False
        self._suspend_dirty = True
        self.compound_name_edit.setText(str(data.get("name", "")))
        self._compound_description_buffer = str(data.get("description", ""))
        self._compound_description_present = "description" in data
        raw_steps = data.get("steps")
        self._steps_buffer = deep_copy(raw_steps) if isinstance(raw_steps, list) else []
        self._refresh_steps_list()
        self._suspend_dirty = False
        self._dirty = False
        self._update_operation_bar()

    def _clear_compound_editor(self) -> None:
        self._compound_state.creating = False
        self._suspend_dirty = True
        self._compound_state.selected = None
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
        """拖拽排序后按界面顺序重排 ``_steps_buffer``。"""
        self._sync_list_order(self.compound_steps_list, self._steps_buffer)

    def _on_compound_steps_moved(self) -> None:
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
        """在右侧面板打开内嵌动作编辑器。"""
        self._embedded.mode = mode
        self._embedded.index = index
        self._embedded.return_panel = return_panel
        self._embedded.original = deep_copy(initial or {})
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
        self._embedded.branch_stack.append(
            {
                "key": key,
                "action_data": action_data,
                "steps": (deep_copy(steps) if isinstance(steps, list) else []),
                "original_steps": (deep_copy(steps) if isinstance(steps, list) else []),
                # 打开分支前的编辑上下文。返回时必须恢复，否则多层嵌套
                # 下提交目标会错位（内层修改写不回任务动作）。
                "parent_mode": self._embedded.mode,
                "parent_index": self._embedded.index,
                "parent_original": self._embedded.original,
            }
        )
        self.embedded_branch_steps_editor.load_steps(
            self._embedded.branch_stack[-1]["steps"]
        )
        self.editor_stack.setCurrentWidget(self.embedded_branch_steps_editor_panel)
        self._update_operation_bar()

    def _restore_branch_parent(self, context: dict[str, Any]) -> None:
        """恢复拥有该分支的动作编辑器上下文。"""

        self._embedded.mode = context.get("parent_mode")
        # context 由 _open_branch_editor 构建，parent_index 键必然存在。
        self._embedded.index = context["parent_index"]
        self._embedded.original = context.get("parent_original")

    def _on_branch_steps_saved(self, steps: list[dict[str, Any]]) -> None:
        if self._embedded.branch_stack:
            context = self._embedded.branch_stack.pop()
            context["action_data"][context["key"]] = deep_copy(steps)
            self._restore_branch_parent(context)
            self._suspend_dirty = True
            try:
                self.embedded_action_editor.load_data(context["action_data"])
            finally:
                self._suspend_dirty = False
            self._mark_dirty()
        self.editor_stack.setCurrentWidget(self.embedded_action_editor_panel)
        self._update_operation_bar()

    def _on_branch_steps_cancelled(self) -> None:
        if self._embedded.branch_stack:
            context = self._embedded.branch_stack.pop()
            self._restore_branch_parent(context)
            self._suspend_dirty = True
            try:
                self.embedded_action_editor.load_data(context["action_data"])
            finally:
                self._suspend_dirty = False
        self.editor_stack.setCurrentWidget(self.embedded_action_editor_panel)
        self._update_operation_bar()

    def _add_branch_step(self) -> None:
        if self._embedded.branch_stack:
            self._embedded.branch_stack[-1]["steps"] = (
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
        if self._embedded.branch_stack:
            self._embedded.branch_stack[-1]["steps"] = deep_copy(steps)
        self.embedded_branch_steps_editor.steps_list.setCurrentRow(row + 1)
        self._mark_dirty()

    def _delete_branch_step(self) -> None:
        row = self.embedded_branch_steps_editor.steps_list.currentRow()
        steps = self.embedded_branch_steps_editor.get_steps()
        if row < 0 or row >= len(steps):
            return
        del steps[row]
        self.embedded_branch_steps_editor.load_steps(steps)
        if self._embedded.branch_stack:
            self._embedded.branch_stack[-1]["steps"] = deep_copy(steps)
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
        if not self._embedded.branch_stack:
            return
        context = self._embedded.branch_stack[-1]
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
        return_panel: QWidget | None = None
        if self._embedded.mode == "task_action":
            if self._embedded.index >= 0:
                self._actions_buffer[self._embedded.index] = data
            else:
                self._actions_buffer.append(data)
            self._refresh_actions_list()
            if self._embedded.index >= 0:
                self.actions_list.setCurrentRow(self._embedded.index)
            else:
                self.actions_list.setCurrentRow(len(self._actions_buffer) - 1)
        elif self._embedded.mode == "compound_step":
            if self._embedded.index >= 0:
                self._steps_buffer[self._embedded.index] = data
            else:
                self._steps_buffer.append(data)
            self._refresh_steps_list()
            if self._embedded.index >= 0:
                self.compound_steps_list.setCurrentRow(self._embedded.index)
            else:
                self.compound_steps_list.setCurrentRow(len(self._steps_buffer) - 1)
        elif self._embedded.mode == "branch_step":
            if self._embedded.branch_stack:
                context = self._embedded.branch_stack[-1]
                if self._embedded.index >= 0:
                    context["steps"][self._embedded.index] = data
                else:
                    context["steps"].append(data)
                self.embedded_branch_steps_editor.load_steps(context["steps"])
                return_panel = self.embedded_branch_steps_editor_panel
            else:
                return_panel = self._embedded.return_panel
        else:
            return_panel = self._embedded.return_panel
        self._embedded.close_editor()
        self._mark_dirty()
        target_panel = return_panel
        if target_panel is None:
            target_panel = self.editor_stack.widget(self.left_tabs.currentIndex())
        self.editor_stack.setCurrentWidget(target_panel)
        self._update_operation_bar()

    def _on_embedded_editor_cancelled(self) -> None:
        return_panel = self._embedded.close_editor()
        target_panel = return_panel
        if target_panel is None:
            target_panel = self.editor_stack.widget(self.left_tabs.currentIndex())
        self.editor_stack.setCurrentWidget(target_panel)
        self._update_operation_bar()

    def _close_embedded_viewer(self) -> None:
        """从内嵌查看器返回打开它之前的面板。"""
        target_panel = self._embedded.previous_panel
        self._embedded.previous_panel = None
        if target_panel is not None:
            self.editor_stack.setCurrentWidget(target_panel)
        else:
            self.editor_stack.setCurrentIndex(self.left_tabs.currentIndex())

    def _commit_embedded_editors(self) -> bool:
        """在保存父对象前，逐层提交嵌套的内嵌编辑器。"""

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
        # 循环上限只是防御：嵌套超过上限时不能静默丢数据，
        # 提示用户逐层退出后再保存。
        if self._embedded.branch_stack or self.editor_stack.currentWidget() in (
            self.embedded_action_editor_panel,
            self.embedded_branch_steps_editor_panel,
        ):
            QMessageBox.warning(
                self, "无法保存", "编辑嵌套层级过深，请逐层返回后再保存。"
            )
            return False
        return True

    def _leave_embedded_editor(self) -> bool:
        """处理未保存修改后退出一层内嵌编辑；用户取消则返回 False。"""

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
        return self._dirty or self._embedded_editor_has_changes()

    def go_back(self) -> bool:
        """返回上一层级，同时保护未保存的编辑内容。

        返回 True 表示本次返回已被消费（弹出了确认框或退出一层），调用方不应
        离开本页；返回 False 表示无需保护或未保存修改已处理完毕，可以关闭本页。
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
        """为调试运行打开内嵌运行输出面板。"""
        self.embedded_run_viewer.start_run(task_name)
        self._embedded.previous_panel = self.editor_stack.currentWidget()
        self.editor_stack.setCurrentWidget(self.embedded_run_viewer_panel)

    def append_run_log(self, message: str) -> None:
        """把 worker 的一条日志追加到内嵌运行输出。"""
        self.embedded_run_viewer.append_log(message)

    def set_run_progress(self, index: int, total: int, description: str) -> None:
        self.embedded_run_viewer.set_progress(index, total, description)

    def finish_run(self, result: RunResult) -> None:
        """在内嵌运行输出中显示最终结果。"""
        self.embedded_run_viewer.finish_run(result)

    def abort_run(self, message: str) -> None:
        """运行前的准备失败时，把内嵌运行输出标记为未运行。"""
        self.embedded_run_viewer.abort_run(message)

    def _on_embedded_ui_tree_action_inserted(self, action: dict[str, Any]) -> None:
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

    def _on_embedded_ui_tree_try_click_requested(
        self, x: int, y: int, _label: str
    ) -> None:
        """复用抓取 UI 树时的 ADB 会话，点击一次所选节点。"""
        if self._ui_tree_adb is None:
            QMessageBox.warning(self, "无法尝试点击", "ADB 未连接，请先抓取 UI 树。")
            return
        adb = self._ui_tree_adb
        self.spawn_background(
            lambda: adb.tap(x, y),
            None,
            lambda message: QMessageBox.warning(self, "尝试点击失败", message),
        )

    def _save_task(self) -> bool:
        task_id = self.task_id_edit.text().strip()
        name = self.task_name_edit.text().strip()
        package = self.task_package_edit.text().strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", task_id):
            QMessageBox.warning(
                self,
                "无法保存",
                "任务 id 只能包含字母、数字、下划线和短横线，且以字母或数字开头。",
            )
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
        previous_id = self._task_state.selected
        target = self.base_directory / "config" / "tasks" / f"{task_id}.json"
        if previous_id != task_id and (task_id in self._tasks or target.exists()):
            QMessageBox.warning(
                self,
                "无法保存",
                f"任务 id 已存在（文件已存在）：{task_id}\n请改名后重试。",
            )
            return False
        validation_errors = self._validate_task_actions(actions)
        if validation_errors:
            QMessageBox.warning(
                self, "无法保存", "动作校验失败：\n" + "\n".join(validation_errors)
            )
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
            write_json_file(target, data)
        except OSError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return False
        if previous_id and previous_id != task_id and old_path is not None:
            mode = str(self.cleanup_mode_combo.currentData() or "recycle")

            def _restore_task() -> None:
                if old_data is not None:
                    self._tasks[previous_id] = old_data
                self._task_state.selected = previous_id
                self._refresh_task_list(select_id=previous_id)

            if self._rollback_rename(old_path, target, mode, _restore_task):
                return False
        if previous_id and previous_id != task_id:
            self._tasks.pop(previous_id, None)
            self._task_state.unsaved.discard(previous_id)
        self._tasks[task_id] = data
        self._task_state.unsaved.discard(task_id)
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
        if (
            isinstance(execution_counts, dict)
            and previous_id
            and previous_id != task_id
            and previous_id in execution_counts
        ):
            execution_counts[task_id] = execution_counts.pop(previous_id)
        try:
            self.settings = update_settings(self.settings_path, self.settings)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self, "顺序保存失败", f"任务已保存，但任务顺序未持久化：{exc}"
            )
        self._task_state.selected = task_id
        self._task_state.creating = False
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

    # 动作

    def _on_action_selection_changed(self, _row: int) -> None:
        self._update_operation_bar()

    def _on_step_selection_changed(self, _row: int) -> None:
        self._update_operation_bar()

    def _refresh_actions_list(self) -> None:
        self._refresh_list(self.actions_list, self._actions_buffer)

    def _refresh_list(self, qlist: QListWidget, buffer: list[dict[str, Any]]) -> None:
        """根据数据缓冲区重建带序号的描述列表。"""
        qlist.blockSignals(True)
        qlist.clear()
        for index, data in enumerate(buffer, start=1):
            description = describe_action(str(data.get("type", "")), data)
            item = QListWidgetItem(f"{index}. {description}")
            item.setData(Qt.ItemDataRole.UserRole, index - 1)
            qlist.addItem(item)
        qlist.blockSignals(False)
        self._update_operation_bar()

    def _sync_list_order(
        self, qlist: QListWidget, buffer: list[dict[str, Any]]
    ) -> None:
        """拖拽排序后按 ``qlist`` 的界面顺序重排 ``buffer``。"""
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
        """拖拽排序后按界面顺序重排 ``_actions_buffer``。"""
        self._sync_list_order(self.actions_list, self._actions_buffer)

    def _on_actions_rows_moved(self) -> None:
        self._sync_actions_from_list()
        self._mark_dirty()

    def _add_action(self) -> None:
        self._show_embedded_editor("task_action", index=-1, initial={})

    def _edit_action(self) -> None:
        self._edit_entry(self.actions_list, self._actions_buffer, "task_action", "动作")

    def _edit_entry(
        self,
        qlist: QListWidget,
        buffer: list[dict[str, Any]],
        mode: str,
        kind_label: str,
    ) -> None:
        """为选中的动作/步骤打开内嵌编辑器。"""
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
        """复制选中的动作/步骤，插入到其下方。"""
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
        row = qlist.currentRow()
        if row < 0 or row >= len(buffer):
            return
        del buffer[row]
        refresh()
        self._mark_dirty()

    def _run_single_action(self) -> None:
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
        """展开选中的单个动作/步骤，发出信号供调试运行。"""
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
        self._embedded.previous_panel = self.editor_stack.currentWidget()
        self.editor_stack.setCurrentWidget(self.embedded_json_viewer_panel)

    def _on_dump_tree_clicked(self) -> None:
        self._dump_ui_tree()

    def _on_copy_package_clicked(self) -> None:
        self.spawn_background(
            lambda: self._query_foreground_package(),
            self._apply_copied_package,
            lambda message: self._show_timed_warning("获取包名失败", message),
        )

    def _query_foreground_package(self) -> str:
        """后台部分：连接设备并读取前台应用包名。"""
        adb = self._connect_to_mumu()
        package = adb.current_package()
        if not package:
            raise ValueError("未识别到前台应用包名。")
        return package

    def _apply_copied_package(self, package: object) -> None:
        QGuiApplication.clipboard().setText(str(package))
        self.feedback_requested.emit(str(package))

    def _on_view_json_clicked(self) -> None:
        if self.left_tabs.currentIndex() == 0:
            self._view_task_json()
        else:
            self._view_compound_json()

    def _dump_ui_tree(self) -> None:
        if self._ui_tree_thread is not None and self._ui_tree_thread.isRunning():
            return

        thread = QThread(self)
        worker = UiTreeDumpWorker(self.settings)
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
        self._embedded.previous_panel = self.editor_stack.currentWidget()
        self.editor_stack.setCurrentWidget(self.embedded_ui_tree_viewer_panel)

    def _on_ui_tree_dump_failed(self, message: str) -> None:
        self._show_timed_warning("抓取 UI 树失败", message)

    def _on_ui_tree_thread_finished(self) -> None:
        self._ui_tree_thread = None
        self._ui_tree_worker = None

    def shutdown(self) -> None:
        """请求 UI 树抓取线程停止并等待其退出。

        由主窗口的关闭流程调用，保证抓取仍在进行时控件（及其子 QThread）
        能被安全销毁。底层 ADB 命令自带超时，因此有界等待必然结束。
        """

        thread = self._ui_tree_thread
        worker = self._ui_tree_worker
        if thread is not None and thread.isRunning() and worker is not None:
            worker.request_stop()
            thread.wait(15000)
        self.wait_background_tasks(30000)

    def _show_timed_warning(self, title: str, message: str) -> None:
        """显示一条五秒后自动消失的非模态通知。"""

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

    def set_pointer_location(self, enabled: bool) -> None:
        """开启或关闭 Android 的屏幕指针坐标悬浮层。

        在后台线程执行；失败时通过 :attr:`pointer_location_failed` 通知，
        以便调用方回退开关状态。
        """

        self.spawn_background(
            lambda: self._set_pointer_location_blocking(enabled),
            None,
            lambda _message: self.pointer_location_failed.emit(enabled),
        )

    def _set_pointer_location_blocking(self, enabled: bool) -> None:
        adb = self._connect_to_mumu()
        adb.shell(
            "settings",
            "put",
            "system",
            "pointer_location",
            "1" if enabled else "0",
        )

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
        if (
            index == 1
            and self.compound_list.currentRow() < 0
            and self.compound_list.count()
        ):
            self.compound_list.setCurrentRow(0)
        self._update_operation_bar()

    # 样式

    @staticmethod
    def _style_sheet() -> str:
        return (
            "\n"
            + _s.PANEL_BASE_QSS
            + "            QDialog { background: #f2f6f4; color: #193331; }\n"
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
            + _s.CARD_TITLE_QSS
            + _s.OCR_FEEDBACK_QSS
            + "            QFormLayout QLabel { color: #49615f; font-size: 13px; font-weight: 500; }\n"
            + _s.MESSAGE_BOX_QSS
            + _s.SCROLLBAR_QSS
            + _s.COMMON_CONTROLS_QSS
            + "        "
        )
