"""任务动作与复合动作的自然语言表单编辑器。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import styles as _s
from .action_schema import (
    CLICK_LOCATES,
    CLICK_UI_TARGETS,
    COMPOUND_TYPE,
    DETECT_LOCATES,
    DETECT_TARGETS,
    PRIMITIVE_TYPES,
    ParamSpec,
    describe_action,
    specs_for,
)
from .helpers import deep_copy
from .message_box import QMessageBox
from .settings_dialog import ElidedLabel, SettingsComboBox

ACTION_TYPE_LABELS: dict[str, str] = {
    "stop": "退出应用",
    "launch": "启动应用",
    "wait": "等待",
    "back": "返回",
    "click": "点击",
    "swipe": "滑动",
    "swipe_until": "滑动直到",
    "detect": "检测",
    "if": "分支",
    "loop_until": "循环直到",
    "capture_screenshot": "截图",
    "compound": "复合动作",
}

OPTION_LABELS: dict[str, str] = {
    "text": "文本",
    "ui": "UI",
    "resource_id": "ID",
    "coordinate": "坐标",
    "ocr": "OCR",
    "exact": "精确",
    "fuzzy": "模糊",
}


class _StepsFieldLabel(QWidget):
    """两行式表单标签：行标题，下方是步骤计数。

    用作 actions 字段在 QFormLayout 中的标签单元格，让“N 个步骤”
    计数紧贴行标题下方，而不是落在按钮一侧。
    """

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.summary = ElidedLabel("0 个步骤")
        self.summary.setStyleSheet("color: #6e8580;")
        layout.addWidget(title_label)
        layout.addWidget(self.summary)

    def set_summary_text(self, text: str) -> None:
        self.summary.setText(text)


class _ActionStepsField(QWidget):
    """嵌套动作列表字段，可把编辑委托给外部宿主。"""

    changed = Signal()

    # 能舒适容纳编辑/清空按钮的最小宽度。
    _FIELD_MIN_WIDTH = 220

    def __init__(
        self,
        parent: QWidget | None = None,
        label: str = "步骤",
        key: str = "",
    ) -> None:
        super().__init__(parent)
        self._steps: list[dict[str, Any]] = []
        self._key = key
        self._edit_handler: Callable[[str, list[dict[str, Any]]], None] | None = None
        self._summary_label: _StepsFieldLabel | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addStretch(1)
        edit_button = QPushButton("编辑")
        edit_button.clicked.connect(self._edit_steps)
        clear_button = QPushButton("清空")
        clear_button.clicked.connect(self._clear_steps)
        layout.addWidget(edit_button)
        layout.addWidget(clear_button)
        self.setMinimumWidth(self._FIELD_MIN_WIDTH)
        self._refresh()

    def set_summary_label(self, label: _StepsFieldLabel) -> None:
        """绑定两行式表单标签，其计数行由本字段负责更新。"""
        self._summary_label = label
        self._refresh()

    def set_edit_handler(
        self,
        handler: Callable[[str, list[dict[str, Any]]], None] | None,
    ) -> None:
        self._edit_handler = handler

    def set_steps(self, steps: list[dict[str, Any]]) -> None:
        self._steps = deep_copy(steps) if isinstance(steps, list) else []
        self._refresh()

    def get_steps(self) -> list[dict[str, Any]]:
        return deep_copy(self._steps)

    def _refresh(self) -> None:
        text = f"{len(self._steps)} 个步骤"
        if self._summary_label is not None:
            self._summary_label.set_summary_text(text)

    def _edit_steps(self) -> None:
        if self._edit_handler is not None:
            self._edit_handler(self._key, self.get_steps())

    def _clear_steps(self) -> None:
        self._steps = []
        self._refresh()
        self.changed.emit()


class _ActionFormMixin:
    """动作表单构建与取值的共享逻辑（mixin；不是控件）。

    具体类必须提供：``type_combo``、``_params_form``、``_field_widgets``、
    ``_active_specs``、``_initial``、``_compound_library`` 与
    ``_allow_compound``。
    """

    _ACTION_FIELD_WIDTH = 200

    def _build_type_section(self) -> tuple[QFormLayout, QWidget]:
        """构建动作类型下拉框与参数表单区域（由具体编辑器共享）。"""
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        self.type_combo = SettingsComboBox()
        for action_type in PRIMITIVE_TYPES:
            self.type_combo.addItem(
                ACTION_TYPE_LABELS.get(action_type, action_type), action_type
            )
        if self._allow_compound:
            self.type_combo.addItem(
                ACTION_TYPE_LABELS.get(COMPOUND_TYPE, COMPOUND_TYPE), COMPOUND_TYPE
            )
        self.type_combo.currentIndexChanged.connect(self._rebuild_form)
        form.addRow("动作类型", self.type_combo)
        type_label_item = form.itemAt(0, QFormLayout.ItemRole.LabelRole)
        if type_label_item is not None and type_label_item.widget() is not None:
            type_label_item.widget().setMinimumWidth(80)
        self._align_form_fields(form)
        container = QWidget()
        container.setObjectName("actionParamsContainer")
        self._params_form = QFormLayout(container)
        self._params_form.setContentsMargins(0, 0, 0, 0)
        self._params_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._params_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint
        )
        self._params_form.setHorizontalSpacing(14)
        self._params_form.setVerticalSpacing(10)
        return form, container

    @staticmethod
    def _align_form_labels(form: QFormLayout) -> None:
        for row in range(form.rowCount()):
            label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            label = label_item.widget() if label_item is not None else None
            if label is not None:
                label.setMinimumWidth(80)

    @classmethod
    def _align_form_fields(cls, form: QFormLayout) -> None:
        for row in range(form.rowCount()):
            field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            field = field_item.widget() if field_item is not None else None
            if field is None or isinstance(field, _ActionStepsField):
                # 步骤字段自行管理宽度，因此其编辑/清空
                # 按钮总能放得下；强制 200px 会裁切按钮文字。
                continue
            field.setFixedWidth(cls._ACTION_FIELD_WIDTH)

    def _configure_steps_field(self, _widget: QWidget) -> None:
        """创建 ``_ActionStepsField`` 后接线用的钩子；默认空操作。"""

    def _load_initial(self) -> None:
        action_type = str(self._initial.get("type", "click"))
        index = self.type_combo.findData(action_type)
        if index >= 0:
            self.type_combo.setCurrentIndex(index)
        self._rebuild_form()
        if action_type == COMPOUND_TYPE:
            name = str(self._initial.get("name", ""))
            name_combo = self._field_widgets.get("name")
            if isinstance(name_combo, QComboBox) and name:
                name_index = name_combo.findData(name)
                if name_index >= 0:
                    name_combo.setCurrentIndex(name_index)
            self._rebuild_form()
            return
        for key in ("locate", "mode", "target"):
            if key not in self._initial:
                continue
            widget = self._field_widgets.get(key)
            if isinstance(widget, QComboBox):
                self._set_widget_value(widget, self._initial[key])
        self._rebuild_form()
        for key, widget in list(self._field_widgets.items()):
            if key in ("locate", "mode", "target") or key not in self._initial:
                continue
            self._set_widget_value(widget, self._initial[key])

    def _rebuild_form(self) -> None:
        form = self._params_form
        if form is None:
            return
        old_widgets = dict(self._field_widgets)
        locate = (
            old_widgets["locate"].currentData()
            if isinstance(old_widgets.get("locate"), QComboBox)
            else None
        )
        target = (
            old_widgets["target"].currentData()
            if isinstance(old_widgets.get("target"), QComboBox)
            else None
        )
        compound_name = (
            old_widgets["name"].currentData()
            if isinstance(old_widgets.get("name"), QComboBox)
            else None
        )
        while form.rowCount():
            taken = form.takeRow(0)
            for item in (taken.labelItem, taken.fieldItem):
                if item is not None:
                    widget = item.widget()
                    if widget is not None:
                        widget.deleteLater()
        self._field_widgets.clear()
        self._active_specs.clear()
        action_type = self.type_combo.currentData()
        if action_type == COMPOUND_TYPE:
            self._build_compound_fields(form, compound_name)
            self._align_form_labels(form)
            self._align_form_fields(form)
            return
        params: dict[str, Any] = {}
        if action_type == "click":
            locate = locate if locate in CLICK_LOCATES else "ui"
            locate_combo = SettingsComboBox()
            for option in CLICK_LOCATES:
                locate_combo.addItem(OPTION_LABELS.get(option, option), option)
            locate_combo.setCurrentIndex(locate_combo.findData(locate))
            locate_combo.currentIndexChanged.connect(self._rebuild_form)
            self._field_widgets["locate"] = locate_combo
            form.addRow("点击目标*", locate_combo)
            params["locate"] = locate
            if locate == "ui":
                target = target if target in CLICK_UI_TARGETS else "text"
                target_combo = SettingsComboBox()
                for option in CLICK_UI_TARGETS:
                    target_combo.addItem(OPTION_LABELS.get(option, option), option)
                target_combo.setCurrentIndex(target_combo.findData(target))
                target_combo.currentIndexChanged.connect(self._rebuild_form)
                self._field_widgets["target"] = target_combo
                form.addRow("UI 目标*", target_combo)
                params["target"] = target
        elif action_type in {"detect", "swipe_until"}:
            locate = locate if locate in DETECT_LOCATES else "ocr"
            locate_combo = SettingsComboBox()
            for option in DETECT_LOCATES:
                locate_combo.addItem(OPTION_LABELS.get(option, option), option)
            locate_combo.setCurrentIndex(locate_combo.findData(locate))
            locate_combo.currentIndexChanged.connect(self._rebuild_form)
            self._field_widgets["locate"] = locate_combo
            form.addRow("检测来源*", locate_combo)
            params["locate"] = locate
            if locate == "ui":
                target = target if target in DETECT_TARGETS else "text"
                target_combo = SettingsComboBox()
                for option in DETECT_TARGETS:
                    target_combo.addItem(OPTION_LABELS.get(option, option), option)
                target_combo.setCurrentIndex(target_combo.findData(target))
                target_combo.currentIndexChanged.connect(self._rebuild_form)
                self._field_widgets["target"] = target_combo
                form.addRow("UI 目标*", target_combo)
                params["target"] = target
        for spec in specs_for(action_type, params):
            widget = self._create_field_widget(spec)
            self._configure_steps_field(widget)
            self._field_widgets[spec.key] = widget
            self._active_specs[spec.key] = spec
            row_label = spec.label + ("*" if spec.required else "")
            if isinstance(widget, _ActionStepsField):
                # 两行式标签单元格：标题行下方带"N 个步骤"计数，
                # 使按钮保持在字段行上。
                label_widget = _StepsFieldLabel(row_label)
                widget.set_summary_label(label_widget)
                form.addRow(label_widget, widget)
            else:
                form.addRow(row_label, widget)
        self._align_form_labels(form)
        self._align_form_fields(form)

    def _build_compound_fields(
        self, form: QFormLayout, selected_name: str | None
    ) -> None:
        combo = SettingsComboBox()
        for name in sorted(self._compound_library):
            combo.addItem(name, name)
        if selected_name:
            index = combo.findData(selected_name)
            combo.setCurrentIndex(index if index >= 0 else -1)
        self._field_widgets["name"] = combo
        form.addRow("复合动作*", combo)

    def _create_field_widget(self, spec: ParamSpec) -> QWidget:
        widget: QWidget
        if spec.kind == "number":
            widget = QLineEdit()
            widget.setValidator(
                QDoubleValidator(-1_000_000_000, 1_000_000_000, 3, widget)
            )
            if spec.default is not None:
                widget.setPlaceholderText(str(spec.default))
            elif spec.placeholder:
                # 无 default 的数字字段（如启动后等待）直接把占位值写成数值。
                widget.setPlaceholderText(spec.placeholder)
        elif spec.kind == "bool":
            widget = QCheckBox()
            widget.setText("是")
            if spec.default is True:
                widget.setChecked(True)
        elif spec.kind == "select":
            widget = SettingsComboBox()
            for option in spec.options:
                widget.addItem(OPTION_LABELS.get(option, option), option)
            if spec.default is not None:
                index = widget.findData(spec.default)
                if index >= 0:
                    widget.setCurrentIndex(index)
        elif spec.kind == "actions":
            widget = _ActionStepsField(label=spec.label, key=spec.key)
        elif spec.kind == "list":
            widget = QLineEdit()
            widget.setPlaceholderText(spec.placeholder or "例如：A,B")
        else:
            widget = QLineEdit()
            widget.setPlaceholderText(spec.placeholder)
        return widget

    def _set_widget_value(self, widget: QWidget, value: Any) -> None:
        if isinstance(widget, _ActionStepsField):
            widget.set_steps(value if isinstance(value, list) else [])
            return
        if isinstance(widget, QComboBox):
            index = widget.findData(value)
            if index >= 0:
                widget.setCurrentIndex(index)
            return
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
            return
        if isinstance(widget, QLineEdit):
            if isinstance(value, list):
                widget.setText(", ".join(str(item) for item in value))
            else:
                widget.setText(str(value))

    def _widget_value(self, widget: QWidget, spec: ParamSpec) -> tuple[Any, str | None]:
        kind = spec.kind
        if isinstance(widget, _ActionStepsField):
            steps = widget.get_steps()
            return (steps if steps else None), None
        if kind == "value":
            text = widget.text().strip()
            if not text:
                if spec.required:
                    return None, f"{spec.label}不能为空"
                return None, None
            if text.lower() in {"true", "false"}:
                return text.lower() == "true", None
            return text, None
        if kind == "text":
            text = widget.text().strip()
            if not text:
                if spec.required:
                    return None, f"{spec.label}不能为空"
                return None, None
            return text, None
        if kind == "number":
            text = widget.text().strip()
            if not text:
                if spec.required:
                    return None, f"{spec.label}不能为空"
                return None, None
            try:
                number = float(text)
            except ValueError:
                return None, f"{spec.label}必须是数字"
            return (int(number) if number.is_integer() else number), None
        if kind == "bool":
            return (True if widget.isChecked() else None), None
        if kind == "list":
            items = [
                item.strip()
                for item in widget.text().replace("，", ",").split(",")
                if item.strip()
            ]
            if not items:
                if spec.required:
                    return None, f"{spec.label}不能为空"
                return None, None
            return items, None
        if kind == "select":
            return widget.currentData(), None
        return None, None

    def _apply_locate_target(self, data: dict[str, Any]) -> None:
        """把 locate/target 下拉框取值写入 click/detect/swipe_until 动作数据。"""
        locate_combo = self._field_widgets.get("locate")
        if isinstance(locate_combo, QComboBox):
            data["locate"] = locate_combo.currentData()
        target_combo = self._field_widgets.get("target")
        if isinstance(target_combo, QComboBox):
            data["target"] = target_combo.currentData()

    def collect(self) -> dict[str, Any] | None:
        action_type = self.type_combo.currentData()
        if action_type == COMPOUND_TYPE:
            name_combo = self._field_widgets.get("name")
            name = (
                name_combo.currentData() if isinstance(name_combo, QComboBox) else None
            )
            if not name:
                QMessageBox.warning(self, "无法保存", "请选择复合动作。")
                return None
            data: dict[str, Any] = {
                key: deep_copy(value)
                for key, value in self._initial.items()
                if key in {"description"}
            }
            data.update({"type": COMPOUND_TYPE, "name": str(name)})
            return data
        managed_keys = {"type", "description"}
        managed_keys.update(self._active_specs)
        data = {
            key: deep_copy(value)
            for key, value in self._initial.items()
            if key in managed_keys
        }
        data["type"] = str(action_type)
        if action_type in {"click", "detect", "swipe_until"}:
            self._apply_locate_target(data)
        errors: list[str] = []
        for key, spec in self._active_specs.items():
            widget = self._field_widgets.get(key)
            if widget is None:
                continue
            value, error = self._widget_value(widget, spec)
            if error is not None:
                errors.append(error)
                continue
            if value is not None:
                data[key] = value
        if errors:
            QMessageBox.warning(self, "无法保存", "\n".join(errors))
            return None
        return data

    def snapshot_data(self) -> dict[str, Any]:
        """返回当前表单取值，不校验、不弹窗。"""
        action_type = self.type_combo.currentData()
        if action_type == COMPOUND_TYPE:
            name_combo = self._field_widgets.get("name")
            name = name_combo.currentData() if isinstance(name_combo, QComboBox) else ""
            data: dict[str, Any] = {
                key: deep_copy(value)
                for key, value in self._initial.items()
                if key in {"description"}
            }
            data.update({"type": COMPOUND_TYPE, "name": str(name)})
            return data
        managed_keys = {"type", "description"}
        managed_keys.update(self._active_specs)
        data = {
            key: deep_copy(value)
            for key, value in self._initial.items()
            if key in managed_keys
        }
        data["type"] = str(action_type)
        if action_type in {"click", "detect", "swipe_until"}:
            self._apply_locate_target(data)
        for key, spec in self._active_specs.items():
            widget = self._field_widgets.get(key)
            if widget is None:
                continue
            value = self._snapshot_widget_value(widget, spec)
            if value is not None:
                data[key] = value
        return data

    def _snapshot_widget_value(self, widget: QWidget, spec: ParamSpec) -> Any:
        value, error = self._widget_value(widget, spec)
        if value is not None or error is None:
            return value
        if isinstance(widget, QLineEdit):
            text = widget.text()
            return text if text.strip() else None
        return None


class ActionEditorWidget(QWidget, _ActionFormMixin):
    """单个动作（原始或复合）的可内嵌表单编辑器。"""

    changed = Signal()
    nested_steps_edit_requested = Signal(str, list)

    def __init__(
        self,
        parent: QWidget | None,
        initial: dict[str, Any],
        compound_library: dict[str, dict[str, Any]] | None = None,
        allow_compound: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet(self._style_sheet())
        self.action_data: dict[str, Any] | None = None
        self._initial = dict(initial)
        self._compound_library = dict(compound_library or {})
        self._allow_compound = allow_compound
        self._field_widgets: dict[str, QWidget] = {}
        self._active_specs: dict[str, ParamSpec] = {}
        self._build_ui()
        self._load_initial()
        self._wire_form_changes()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        title_label = QLabel("编辑动作")
        title_label.setObjectName("settingsCardTitle")
        layout.addWidget(title_label)
        form, container = self._build_type_section()
        layout.addLayout(form)
        layout.addWidget(container)
        layout.addStretch(1)

    @staticmethod
    def _style_sheet() -> str:
        return (
            "\n" + _s.PANEL_BASE_QSS + "            QLabel {\n"
            "                color: #193331;\n"
            "            }\n"
            + _s.CARD_TITLE_QSS
            + _s.MESSAGE_BOX_QSS
            + _s.COMMON_CONTROLS_QSS
            + "        "
        )

    def _configure_steps_field(self, widget: QWidget) -> None:
        if isinstance(widget, _ActionStepsField):
            widget.set_edit_handler(self._handle_nested_steps_edit)

    def _handle_nested_steps_edit(
        self,
        key: str,
        steps: list[dict[str, Any]],
    ) -> None:
        self.nested_steps_edit_requested.emit(key, steps)

    def _on_form_changed(self, *_args: Any) -> None:
        self._wire_form_changes()
        self.changed.emit()

    def _wire_form_changes(self) -> None:
        for widget in [self.type_combo, *self._field_widgets.values()]:
            if widget.property("_free_form_change_wired"):
                continue
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._on_form_changed)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._on_form_changed)
            elif isinstance(widget, QCheckBox):
                widget.stateChanged.connect(self._on_form_changed)
            elif isinstance(widget, _ActionStepsField):
                widget.changed.connect(self._on_form_changed)
            else:
                continue
            widget.setProperty("_free_form_change_wired", True)

    def load_data(self, data: dict[str, Any]) -> None:
        self._initial = dict(data)
        self._load_initial()
        self._wire_form_changes()


class _ActionListMixin:
    """动作列表编辑器共享的步骤列表逻辑（mixin；不是控件）。

    具体类必须提供 ``_steps`` 和 ``steps_list`` QListWidget 属性。
    """

    def load_steps(self, steps: list[dict[str, Any]]) -> None:
        self._steps = deep_copy(steps) if isinstance(steps, list) else []
        self._refresh_steps()

    def get_steps(self) -> list[dict[str, Any]]:
        return deep_copy(self._steps)

    def _refresh_steps(self) -> None:
        self.steps_list.clear()
        for index, data in enumerate(self._steps, start=1):
            description = describe_action(str(data.get("type", "")), data)
            item = QListWidgetItem(f"{index}. {description}")
            item.setData(Qt.ItemDataRole.UserRole, index - 1)
            self.steps_list.addItem(item)

    def _remove_step(self) -> None:
        row = self.steps_list.currentRow()
        if row < 0 or row >= len(self._steps):
            return
        del self._steps[row]
        self._refresh_steps()


class ActionListEditorWidget(QWidget, _ActionListMixin):
    """if 分支使用的扁平原始动作列表的可内嵌编辑器。"""

    add_step_requested = Signal()
    edit_step_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None, title: str = "步骤") -> None:
        super().__init__(parent)
        self._steps: list[dict[str, Any]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        title_label = QLabel(title)
        title_label.setObjectName("settingsCardTitle")
        add_button = QPushButton("添加步骤")
        add_button.setObjectName("settingsTestButton")
        edit_button = QPushButton("编辑步骤")
        edit_button.setObjectName("settingsTestButton")
        remove_button = QPushButton("删除步骤")
        remove_button.setObjectName("dangerButton")
        add_button.clicked.connect(self._add_step)
        edit_button.clicked.connect(self._edit_step)
        remove_button.clicked.connect(self._remove_step)
        header.addWidget(title_label)
        header.addStretch(1)
        for button in (add_button, edit_button, remove_button):
            header.addWidget(button)
        layout.addLayout(header)

        self.steps_list = QListWidget()
        self.steps_list.setObjectName("settingsTaskList")
        self.steps_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.steps_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.steps_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.steps_list.model().rowsMoved.connect(self._sync_steps_from_list)
        self.steps_list.itemDoubleClicked.connect(lambda _item: self._edit_step())
        layout.addWidget(self.steps_list, 1)
        self._refresh_steps()

    def _sync_steps_from_list(self) -> None:
        new_order: list[dict[str, Any]] = []
        for i in range(self.steps_list.count()):
            item = self.steps_list.item(i)
            if item is None:
                continue
            source_index = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(source_index, int) and 0 <= source_index < len(self._steps):
                new_order.append(self._steps[source_index])
        if len(new_order) == len(self._steps):
            self._steps = new_order
            self.steps_list.blockSignals(True)
            for i in range(self.steps_list.count()):
                item = self.steps_list.item(i)
                if item is not None:
                    item.setData(Qt.ItemDataRole.UserRole, i)
            self.steps_list.blockSignals(False)

    def _add_step(self) -> None:
        self.add_step_requested.emit()

    def _edit_step(self) -> None:
        row = self.steps_list.currentRow()
        if row < 0 or row >= len(self._steps):
            return
        self.edit_step_requested.emit(row)
