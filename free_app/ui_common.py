"""各 UI 模块共享的元素：样式表、中文消息框、控件工厂与共享控件。

由原 ``styles.py`` + ``message_box.py`` + ``ui_common.py`` 合并而成：样式片段按
``_s.XXX`` 引用（``_s`` 指向本模块）；中文消息框 ``QMessageBox`` / ``confirm()``；
按钮工厂与尺寸常量；页头 / 卡片标题 / 对话框底栏 / 滚动页面骨架；
跨模块共享控件（ElidedLabel / SettingsComboBox / ExecutionCountComboBox /
ModelRowCard / confirm_dialog）。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import (
    QMessageBox as _QtMessageBox,
)

# 合并前 styles.py 以 ``_s.XXX`` 引用同一批片段；本模块即样式来源，保留别名。
# 必须在样式片段之前建立，供模块级求值（如 PAGE_BASE_QSS）使用。
_s = sys.modules[__name__]

# ==================================================== 样式表片段
# 原 free_app/styles.py，逐字并入。

# 绿色"保存"/主按钮共用的垂直渐变。

GREEN_VGRAD = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a9385, stop:1 #137f73)"

GREEN_VGRAD_HOVER = (

    "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a9385, stop:1 #0e6e64)"

)

GREEN_DIAG = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1a9385, stop:1 #137f73)"

DARK_PRESSED_VGRAD = (

    "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #137f73, stop:1 #095b54)"

)

# 表单控件框（QLineEdit / QSpinBox / QTextEdit / QPlainTextEdit）。

UNIFIED_FORM_CONTROLS_QSS = (

    "            QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {\n"

    "                background: #ffffff;\n"

    "                border: 1px solid #cbdcd6;\n"

    "                border-radius: 8px;\n"

    "                color: #193331;\n"

    "                selection-background-color: #cce8e0;\n"

    "            }\n"

    "            QLineEdit, QSpinBox, QDoubleSpinBox {\n"

    "                min-height: 40px;\n"

    "                max-height: 40px;\n"

    "                padding: 0 12px;\n"

    "            }\n"

    "            QTextEdit, QPlainTextEdit {\n"

    "                padding: 8px 12px;\n"

    "            }\n"

    "            QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QTextEdit:hover, QPlainTextEdit:hover {\n"

    "                background: #ffffff;\n"

    "                border-color: #83b9ad;\n"

    "            }\n"

    "            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {\n"

    "                background: #ffffff;\n"

    "                border: 2px solid #2b9b8b;\n"

    "            }\n"

)

# 下拉框（基础、下拉箭头、弹层列表视图）。所有页面共用。

UNIFIED_COMBO_QSS = (

    "            QComboBox {\n"

    "                min-height: 40px;\n"

    "                padding: 0 34px 0 13px;\n"

    "                border: 1px solid #cbdcd6;\n"

    "                border-radius: 8px;\n"

    "                background: #ffffff;\n"

    "                color: #193331;\n"

    "                selection-background-color: #d6ebe5;\n"

    "                max-height: 40px;\n"

    "            }\n"

    "            QComboBox:hover {\n"

    "                border-color: #83b9ad;\n"

    "                background: #ffffff;\n"

    "            }\n"

    "            QComboBox::down-arrow {\n"

    "                image: none;\n"

    "                width: 0px;\n"

    "                height: 0px;\n"

    "            }\n"

    "            QComboBox:focus {\n"

    "                border: 2px solid #2b9b8b;\n"

    "                background: #ffffff;\n"

    "            }\n"

    "            QComboBox::drop-down {\n"

    "                subcontrol-origin: padding;\n"

    "                subcontrol-position: top right;\n"

    "                width: 32px;\n"

    "                border: none;\n"

    "                border-left: none;\n"

    "                border-top-right-radius: 8px;\n"

    "                border-bottom-right-radius: 8px;\n"

    "                background: #ffffff;\n"

    "            }\n"

    "            QComboBox QAbstractItemView {\n"

    "                min-width: 120px;\n"

    "                background: #ffffff;\n"

    "                color: #244340;\n"

    "                border: 1px solid #b9d3ca;\n"

    "                border-radius: 7px;\n"

    "                padding: 6px;\n"

    "                outline: 0;\n"

    "                selection-background-color: #d6ebe5;\n"

    "                selection-color: #0c6e63;\n"

    "            }\n"

    "            QComboBox QAbstractItemView::item {\n"

    "                min-height: 34px;\n"

    "                padding: 0 12px;\n"

    "                border-radius: 6px;\n"

    "            }\n"

    "            QComboBox QAbstractItemView::item:hover {\n"

    "                background: #edf7f4;\n"

    "                color: #0c6e63;\n"

    "            }\n"

)

# 复选框（基础 + 指示器）。动作编辑器共用。

UNIFIED_CHECKBOX_QSS = (

    "            QCheckBox {\n"

    "                color: #193331;\n"

    "                spacing: 8px;\n"

    "            }\n"

    "            QCheckBox::indicator {\n"

    "                width: 18px;\n"

    "                height: 18px;\n"

    "                border: 2px solid #b9d3ca;\n"

    "                border-radius: 4px;\n"

    "                background: #ffffff;\n"

    "            }\n"

    "            QCheckBox::indicator:checked {\n"

    "                background: " + GREEN_DIAG + ";\n"

    "                border-color: #137f73;\n"

    "            }\n"

    "            QCheckBox::indicator:hover {\n"

    "                border-color: #83b9ad;\n"

    "            }\n"

)

# 通用按钮（基础 + 语义变体 + 交互状态）。

# 动作编辑器对话框与任务管理页控件共用。

UNIFIED_BUTTONS_QSS = (

    "            QPushButton, QToolButton {\n"

    "                min-height: 40px;\n"

    "                min-width: 88px;\n"

    "                padding: 0 16px;\n"

    "                border: 1px solid #cbdcd6;\n"

    "                border-radius: 8px;\n"

    "                background: #ffffff;\n"

    "                color: #31504d;\n"

    "                font-weight: 650;\n"

    "            }\n"

    "            QPushButton:hover, QToolButton:hover {\n"

    "                background: #edf7f4;\n"

    "                border-color: #83b9ad;\n"

    "            }\n"

    "            QPushButton:pressed, QToolButton:pressed {\n"

    "                background: #dcece7;\n"

    "                border-color: #65b3a7;\n"

    "            }\n"

    "            QPushButton:disabled, QToolButton:disabled {\n"

    "                color: #9aa9a7;\n"

    "                background: #edf1f0;\n"

    "                border-color: #dce5e2;\n"

    "            }\n"

    "            QPushButton#secondaryButton, QPushButton#settingsTestButton, QPushButton#taskManagerPointerButton, QPushButton#settingsModelAction {\n"

    "                color: #146e65;\n"

    "                background: #e3f2ed;\n"

    "                border-color: #afd5ca;\n"

    "            }\n"

    "            QPushButton#secondaryButton:hover, QPushButton#settingsTestButton:hover, QPushButton#taskManagerPointerButton:hover, QPushButton#settingsModelAction:hover {\n"

    "                background: #d6ebe5;\n"

    "                border-color: #83b9ad;\n"

    "            }\n"

    "            QPushButton#secondaryButton:pressed, QPushButton#settingsTestButton:pressed, QPushButton#taskManagerPointerButton:pressed, QPushButton#settingsModelAction:pressed {\n"

    "                background: #c9e2db;\n"

    "                border-color: #65b3a7;\n"

    "            }\n"

    "            QPushButton#taskManagerPointerButton:checked {\n"

    "                color: #ffffff;\n"

    "                background: #137f73;\n"

    "                border-color: #137f73;\n"

    "            }\n"

    "            QPushButton#taskManagerPointerButton:checked:hover {\n"

    "                background: #0e6e64;\n"

    "                border-color: #0e6e64;\n"

    "            }\n"

    "            QPushButton#settingsSaveButton, QPushButton#taskManagerOperationSave, QPushButton:default {\n"

    "                min-height: 40px;\n"

    "                min-width: 100px;\n"

    "                padding: 0 20px;\n"

    "                color: #ffffff;\n"

    "                background: " + GREEN_VGRAD + ";\n"

    "                border: none;\n"

    "                border-radius: 8px;\n"

    "                font-weight: 700;\n"

    "            }\n"

    "            QPushButton#settingsSaveButton:hover, QPushButton#taskManagerOperationSave:hover, QPushButton:default:hover {\n"

    "                background: " + GREEN_VGRAD_HOVER + ";\n"

    "            }\n"

    "            QPushButton#settingsSaveButton:pressed, QPushButton#taskManagerOperationSave:pressed, QPushButton:default:pressed {\n"

    "                background: " + DARK_PRESSED_VGRAD + ";\n"

    "            }\n"

    "            QPushButton#quietButton, QPushButton#settingsCancelButton {\n"

    "                color: #31504d;\n"

    "                background: #f6f9f8;\n"

    "            }\n"

    "            QPushButton#quietButton:hover, QPushButton#settingsCancelButton:hover {\n"

    "                background: #edf7f4;\n"

    "                border-color: #83b9ad;\n"

    "            }\n"

    "            QPushButton#quietButton:pressed, QPushButton#settingsCancelButton:pressed {\n"

    "                background: #dcece7;\n"

    "            }\n"

    '            QPushButton#dangerButton, QPushButton#settingsModelAction[downloaded="true"] {\n'

    "                color: #a34a42;\n"

    "                background: #fbeae8;\n"

    "                border-color: #e5b9b3;\n"

    "            }\n"

    '            QPushButton#dangerButton:hover, QPushButton#settingsModelAction[downloaded="true"]:hover {\n'

    "                background: #f6d8d4;\n"

    "                border-color: #d88f87;\n"

    "            }\n"

    '            QPushButton#dangerButton:pressed, QPushButton#settingsModelAction[downloaded="true"]:pressed {\n'

    "                background: #efc4be;\n"

    "                border-color: #c9776e;\n"

    "            }\n"

    "            QPushButton#runActionButton {\n"

    "                color: #ffffff;\n"

    "                background: #1976d2;\n"

    "                border-color: #1976d2;\n"

    "                font-weight: 700;\n"

    "            }\n"

    "            QPushButton#runActionButton:hover {\n"

    "                background: #1565c0;\n"

    "                border-color: #1565c0;\n"

    "            }\n"

    "            QPushButton#runActionButton:pressed {\n"

    "                background: #0d47a1;\n"

    "                border-color: #0d47a1;\n"

    "            }\n"

)

# 页面级样式表的唯一入口：所有公共控件的基础规则，

# 按固定顺序排列，使页面无法为同一公共控件追加第二条

# 略有不同的基础规则。

COMMON_CONTROLS_QSS = (

    UNIFIED_FORM_CONTROLS_QSS

    + UNIFIED_COMBO_QSS

    + UNIFIED_CHECKBOX_QSS

    + UNIFIED_BUTTONS_QSS

)

# 嵌入式面板（查看器、对话框、任务管理页）的公共底座：透明背景。

PANEL_BASE_QSS = "            QWidget { background: transparent; color: #193331; }\n"

# 面板内的白色圆角内容区：JSON/运行日志文本框与 UI 树共用同一外观。

PANEL_CONTENT_QSS = (

    "            QPlainTextEdit, QTreeWidget {\n"

    "                background: #ffffff;\n"

    "                border: 1px solid #cbdcd6;\n"

    "                border-radius: 8px;\n"

    "                color: #244340;\n"

    "                outline: 0;\n"

    "            }\n"

    "            QPlainTextEdit {\n"

    "                padding: 10px;\n"

    "                selection-background-color: #cce8e0;\n"

    "                font-size: 13px;\n"

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

)

# 设置页与所有应用视图共用的细滚动条。

SCROLLBAR_QSS = (

    "            QScrollBar:vertical {\n"

    "                width: 6px;\n"

    "                margin: 4px 2px 4px 0;\n"

    "                background: transparent;\n"

    "            }\n"

    "            QScrollBar:horizontal {\n"

    "                height: 6px;\n"

    "                margin: 0 4px 2px 4px;\n"

    "                background: transparent;\n"

    "            }\n"

    "            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {\n"

    "                min-height: 20px;\n"

    "                min-width: 20px;\n"

    "                border-radius: 3px;\n"

    "                background: #b9d3ca;\n"

    "            }\n"

    "            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {\n"

    "                background: #8bbeb2;\n"

    "            }\n"

    "            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,\n"

    "            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {\n"

    "                width: 0; height: 0; border: none; background: transparent;\n"

    "            }\n"

    "            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,\n"

    "            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {\n"

    "                background: transparent;\n"

    "            }\n"

)

# 父控件使用透明背景时，保持系统消息框可读。

# 没有这些规则，Windows 可能在保留应用深色文字颜色的同时

# 暴露出黑色对话框主体。

MESSAGE_BOX_QSS = (

    "            QMessageBox, QMessageBox QWidget {\n"

    "                background: #f2f6f4;\n"

    "                color: #193331;\n"

    "            }\n"

    "            QMessageBox QLabel {\n"

    "                background: #f2f6f4;\n"

    "                color: #193331;\n"

    "            }\n"

    "            QMessageBox QPushButton {\n"

    "                min-width: 72px;\n"

    "                min-height: 40px;\n"

    "                padding: 0 14px;\n"

    "                border: 1px solid #cbdad6;\n"

    "                border-radius: 8px;\n"

    "                background: #ffffff;\n"

    "                color: #31504d;\n"

    "                font-weight: 650;\n"

    "            }\n"

    "            QMessageBox QPushButton:hover {\n"

    "                background: #edf7f4;\n"

    "                border-color: #83b9ad;\n"

    "            }\n"

    "            QMessageBox QPushButton:pressed {\n"

    "                background: #dcece7;\n"

    "                border-color: #65b3a7;\n"

    "            }\n"

    "            QMessageBox QPushButton#messageBoxAction {\n"

    "                min-width: 96px;\n"

    "                min-height: 40px;\n"

    "                padding: 0 16px;\n"

    "            }\n"

    "            QMessageBox QPushButton:default {\n"

    "                min-width: 96px;\n"

    "                min-height: 40px;\n"

    "                padding: 0 16px;\n"

    "                color: #ffffff;\n"

    "                background: " + GREEN_VGRAD + ";\n"

    "                border: none;\n"

    "                border-radius: 8px;\n"

    "                font-weight: 700;\n"

    "            }\n"

    "            QMessageBox QPushButton:default:hover {\n"

    "                background: " + GREEN_VGRAD_HOVER + ";\n"

    "            }\n"

    "            QMessageBox QPushButton:default:pressed {\n"

    "                background: " + DARK_PRESSED_VGRAD + ";\n"

    "            }\n"

)

# 面板小标签。多个对话框/控件共用。

CARD_TITLE_QSS = (

    "            QLabel#settingsCardTitle {\n"

    "                color: #244340;\n"

    "                font-size: 14px;\n"

    "                font-weight: 700;\n"

    "                padding: 4px 0;\n"

    "            }\n"

)

OCR_FEEDBACK_QSS = (

    "            QLabel#settingsOcrFeedback {\n"

    "                color: #6e8580;\n"

    "                font-size: 12px;\n"

    "                padding: 4px 8px;\n"

    "                background: #f7faf9;\n"

    "                border-radius: 4px;\n"

    "            }\n"

)

# SettingsComboBox 自绘下拉弹层，而非使用 Qt 原生弹层。

# 该弹层的公共下拉样式也统一放在这里。

COMBO_POPUP_QSS = """

    QFrame#settingsComboPopup {

        background: transparent;

        border: none;

    }

    QListWidget#settingsComboPopupList {

        background: transparent;

        border: none;

        outline: none;

        padding: 0;

        color: #244340;

    }

    QListWidget#settingsComboPopupList::item {

        min-height: 34px;

        padding: 0 12px;

        border-radius: 6px;

    }

    QListWidget#settingsComboPopupList::item:hover {

        background: #edf7f4;

        color: #0c6e63;

    }

    QListWidget#settingsComboPopupList::item:selected {

        background: #d6ebe5;

        color: #0c6e63;

    }

    QListWidget#settingsComboPopupList::corner {

        width: 0;

        height: 0;

        background: transparent;

        border: none;

    }

    QListWidget#settingsComboPopupList QAbstractScrollArea::corner {

        background: transparent;

        border: none;

    }

    QListWidget#settingsComboPopupList QScrollBar:vertical {

        width: 6px;

        margin: 4px 2px 4px 0;

        background: transparent;

    }

    QListWidget#settingsComboPopupList QScrollBar::handle:vertical {

        min-height: 20px;

        border-radius: 3px;

        background: #b9d3ca;

    }

    QListWidget#settingsComboPopupList QScrollBar::add-line:vertical,

    QListWidget#settingsComboPopupList QScrollBar::sub-line:vertical {

        height: 0;

        border: none;

        background: transparent;

    }

"""

# ====================================================== 中文消息框
# 原 free_app/message_box.py，逐字并入。

class QMessageBox(_QtMessageBox):

    """带中文标准按钮文案的 QMessageBox。

    类保持 Qt 的公共 API 不变，现有调用方与测试仍可使用

    ``QMessageBox.warning`` / ``information``。

    """

    _BUTTON_LABELS = {

        _QtMessageBox.StandardButton.Ok: "确定",

        _QtMessageBox.StandardButton.Save: "保存",

        _QtMessageBox.StandardButton.Discard: "放弃",

        _QtMessageBox.StandardButton.Cancel: "取消",

        _QtMessageBox.StandardButton.Yes: "是",

        _QtMessageBox.StandardButton.No: "否",

        _QtMessageBox.StandardButton.Retry: "重试",

        _QtMessageBox.StandardButton.Ignore: "忽略",

        _QtMessageBox.StandardButton.Abort: "中止",

    }

    @classmethod

    def _show_standard(

        cls,

        parent: Any,

        title: str,

        text: str,

        icon: _QtMessageBox.Icon,

        buttons: _QtMessageBox.StandardButton,

        default_button: _QtMessageBox.StandardButton,

    ) -> _QtMessageBox.StandardButton:

        box = cls(parent)

        box.setWindowTitle(title)

        box.setText(text)

        box.setIcon(icon)

        box.setStandardButtons(buttons)

        for standard_button, label in cls._BUTTON_LABELS.items():

            button = box.button(standard_button)

            if button is not None:

                button.setText(label)

        for button in box.buttons():

            button.setObjectName("messageBoxAction")

        box.setStyleSheet(_s.MESSAGE_BOX_QSS)

        if default_button != _QtMessageBox.StandardButton.NoButton:

            box.setDefaultButton(default_button)

        box.exec()

        clicked = box.clickedButton()

        return box.standardButton(clicked)

    @staticmethod

    def warning(

        parent: Any,

        title: str,

        text: str,

        buttons: _QtMessageBox.StandardButton = _QtMessageBox.StandardButton.Ok,

        default_button: _QtMessageBox.StandardButton = _QtMessageBox.StandardButton.NoButton,

    ) -> _QtMessageBox.StandardButton:

        return QMessageBox._show_standard(

            parent, title, text, _QtMessageBox.Icon.Warning, buttons, default_button

        )

    @staticmethod

    def information(

        parent: Any,

        title: str,

        text: str,

        buttons: _QtMessageBox.StandardButton = _QtMessageBox.StandardButton.Ok,

        default_button: _QtMessageBox.StandardButton = _QtMessageBox.StandardButton.NoButton,

    ) -> _QtMessageBox.StandardButton:

        return QMessageBox._show_standard(

            parent, title, text, _QtMessageBox.Icon.Information, buttons, default_button

        )

def confirm(

    parent: Any,

    title: str,

    text: str,

    *,

    confirm_label: str = "确认",

    cancel_label: str = "取消",

) -> bool:

    """阻塞式确认/取消提示，按钮文案统一为中文。

    结果通过 ``clickedButton()`` 按对象身份比较得出，而非旧实现

    使用的 ``buttons()`` 位置索引。

    """

    box = QMessageBox(parent)

    box.setWindowTitle(title)

    box.setText(text)

    cancel_button = box.addButton(cancel_label, _QtMessageBox.ButtonRole.AcceptRole)

    confirm_button = box.addButton(

        confirm_label, _QtMessageBox.ButtonRole.DestructiveRole

    )

    cancel_button.setObjectName("messageBoxAction")

    confirm_button.setObjectName("messageBoxAction")

    confirm_button.setDefault(True)

    box.setEscapeButton(cancel_button)

    box.setStyleSheet(_s.MESSAGE_BOX_QSS)

    box.exec()

    return box.clickedButton() == confirm_button

# ================================================ 共享控件与工厂
# 原 free_app/ui_common.py（常量、按钮工厂、页头/容器骨架、共享控件）。

# --------------------------------------------------------------- 尺寸尺度

# 成排按钮的统一高度：主页动作栏、二级页面页头工具按钮、对话框底栏都用它。
# 高度交给样式表（``min-height``）决定，按钮本身不锁死高度。
ROW_BUTTON_HEIGHT = 40

# 主页动作栏按钮宽度（任务管理 / 执行全部 / 执行 / 刷新）。
# 只定宽、不锁高：四个按钮同宽，执行状态切换只改文字不改几何，整排不会跳动。
MAIN_ACTION_BUTTON_WIDTH = 128

# 主页齿轮按钮（图标按钮）的宽度；高度与同一排的文字按钮一致。
MAIN_ICON_BUTTON_WIDTH = 48

# 二级页面页头工具按钮宽度（显示坐标 / 获取包名 / 抓取 UI 树 / 查看 JSON）。
TOOL_BUTTON_WIDTH = 108

# 二级页面页头"返回"按钮宽度。
BACK_BUTTON_WIDTH = 74

# 需要容纳较长中文的次级按钮最小宽度（"发送测试邮件"等）。
WIDE_BUTTON_MIN_WIDTH = 120

# 表单里小号下拉框的宽度（执行次数、下载源等）。
OUTLINE_COMBO_WIDTH = 130

# ------------------------------------------------------------------ 样式表

# 二级页面（设置 / 任务管理）页头标题。主页的 ``titleLabel`` 与

# ``brandLabel`` 是主页独有，不进公共样式。

PAGE_TITLE_QSS = (

    "            QLabel#settingsPageTitle {\n"

    "                color: #203637;\n"

    "                font-size: 22px;\n"

    "                font-weight: 750;\n"

    "            }\n"

)

# 页面级样式表的公共前缀：消息框、滚动条、全部公共控件规则与页头标题。

# 各页面在此之后追加自己的例外规则，顺序与拆分前保持一致。

PAGE_BASE_QSS = (

    _s.MESSAGE_BOX_QSS

    + _s.SCROLLBAR_QSS

    + _s.COMMON_CONTROLS_QSS

    + PAGE_TITLE_QSS

)

# -------------------------------------------------------------------- 按钮

def _make_button(

    text: str,

    object_name: str | None,

    size: tuple[int, int] | None,

    minimum: tuple[int, int] | None,

    width: int | None,

) -> QPushButton:

    button = QPushButton(text)

    if object_name is not None:

        button.setObjectName(object_name)

    if size is not None:

        button.setFixedSize(*size)

    if minimum is not None:

        button.setMinimumSize(*minimum)

    if width is not None:

        button.setFixedWidth(width)

    return button

def secondary_button(

    text: str,

    *,

    width: int | None = TOOL_BUTTON_WIDTH,

    object_name: str | None = "secondaryButton",

) -> QPushButton:

    """浅绿次要按钮：**只定宽，高度交给样式表**（``min-height``）。

    全项目成排按钮都用这一种构造，因此主页动作栏、二级页头、对话框底栏的
    按钮高度天然一致；``width=None`` 表示连宽度也不锁（对话框"取消"等按文字走）。

    """

    return _make_button(text, object_name, None, None, width)

def primary_button(

    text: str,

    *,

    object_name: str | None = "primaryButton",

    minimum_height: int | None = None,

) -> QPushButton:

    """主操作按钮（绿色渐变），并设为对话框默认按钮。

    ``minimum_height`` 默认交给 QSS（``min-height: 40px``）决定，与合并前

    的手工写法一致；需要额外约束的调用方自行传入。

    """

    button = _make_button(text, object_name, None, None, None)

    if minimum_height is not None:

        button.setMinimumHeight(minimum_height)

    button.setDefault(True)

    return button

def outline_button(

    text: str,

    *,

    object_name: str | None = "secondaryButton",

    minimum_width: int | None = None,

    height: int | None = None,

) -> QPushButton:

    """表单里的小号次要按钮（"浏览" / "刷新实例" / "测试识别"）。

    默认高度交给 QSS（``min-height: 40px``）；``minimum_width`` 用于兜底，

    避免较长中文标签在缩放分辨率下被裁切。

    """

    button = _make_button(text, object_name, None, None, None)

    if minimum_width is not None:

        button.setMinimumWidth(minimum_width)

    if height is not None:

        button.setMinimumHeight(height)

    return button

def danger_button(

    text: str,

    *,

    object_name: str | None = "dangerButton",

    minimum_width: int | None = None,

) -> QPushButton:

    """不可逆操作按钮（清理日志/截图、删除）。"""

    button = _make_button(text, object_name, None, None, None)

    if minimum_width is not None:

        button.setMinimumWidth(minimum_width)

    return button

def back_button() -> QPushButton:

    """构造页头"返回"按钮；点击行为由调用方连接。

    与同排其它按钮一样只定宽，高度由样式表决定。
    """

    return _make_button("返回", "quietButton", None, None, BACK_BUTTON_WIDTH)

# --------------------------------------------------------------- 标签/页头

def page_title(text: str) -> QLabel:

    """构造二级页面页头标题标签。"""

    return card_title(text, object_name="settingsPageTitle")

def card_title(text: str, *, object_name: str = "settingsCardTitle") -> QLabel:

    """构造卡片/区块标题标签（默认 14px 加粗，样式见 ``styles``）。"""

    label = QLabel(text)

    label.setObjectName(object_name)

    return label

def page_header(

    title: str, on_back: Callable[[], None] | None = None

) -> tuple[QHBoxLayout, QPushButton]:

    """二级页面页头：标题在左、可选"返回"在右。

    返回 ``(布局, 返回按钮)``，调用方可以继续往右侧插入工具按钮。

    """

    header = QHBoxLayout()

    header.setSpacing(10)

    header.addWidget(page_title(title))

    header.addStretch(1)

    button = back_button()

    if on_back is not None:

        button.clicked.connect(on_back)

    header.addWidget(button)

    return header, button

# ------------------------------------------------------------------ 对话框

def dialog_action_row(

    parent: QWidget,

    on_cancel: Callable[[], Any] | None,

    on_save: Callable[[], Any] | None,

    *,

    cancel_text: str = "取消",

    save_text: str = "保存",

    cancel_name: str = "settingsCancelButton",

    save_name: str = "settingsSaveButton",

    spacing: int = 10,

    top_margin: int = 14,

) -> tuple[QHBoxLayout, QPushButton, QPushButton]:

    """对话框底栏：右侧「取消 / 保存」，直接装到 ``parent`` 上。

    返回 ``(布局, 取消按钮, 保存按钮)``；主按钮的"默认按钮"语义由

    :func:`primary_button` 建立。

    """

    row = QHBoxLayout(parent)

    row.setContentsMargins(0, top_margin, 0, 0)

    row.setSpacing(spacing)

    row.addStretch(1)

    cancel_button = secondary_button(

        cancel_text, width=None, object_name=cancel_name

    )

    save_button = primary_button(save_text, object_name=save_name)

    if on_cancel is not None:

        cancel_button.clicked.connect(on_cancel)

    if on_save is not None:

        save_button.clicked.connect(on_save)

    row.addWidget(cancel_button)

    row.addWidget(save_button)

    return row, cancel_button, save_button

# -------------------------------------------------------------------- 容器

def scrollable_page(

    *,

    margins: tuple[int, int, int, int] = (20, 20, 20, 20),

    spacing: int = 18,

    layout_class: type = QVBoxLayout,

) -> tuple[QFrame, QVBoxLayout, QScrollArea, QWidget, Any]:

    """「页面容器 + 细滚动区 + 内容区」骨架（设置页各分页共用）。

    返回 ``(container, page, scroll, content, content_layout)``：

    ``content_layout`` 用来填内容，填完后交给 :func:`mount_scrollable_page`

    组装。调用方把 ``container`` 挂到导航区即可。

    """

    container = QFrame()

    container.setObjectName("settingsTabPage")

    page = QVBoxLayout(container)

    page.setContentsMargins(0, 0, 0, 0)

    scroll = QScrollArea()

    scroll.setObjectName("settingsScroll")

    scroll.setWidgetResizable(True)

    scroll.setFrameShape(QFrame.Shape.NoFrame)

    content = QWidget()

    content_layout = layout_class(content)

    content_layout.setContentsMargins(*margins)

    content_layout.setSpacing(spacing)

    return container, page, scroll, content, content_layout

def mount_scrollable_page(

    page: QVBoxLayout, scroll: QScrollArea, content: QWidget

) -> None:

    """填充完内容后组装「滚动区 + 内容区」：``scroll.setWidget`` → ``page.addWidget``。

    滚动区与内容区都不自带背景，全部由页面样式绘制，否则会出现一层

    与主题不一致的灰底。

    """

    scroll.setWidget(content)

    scroll.viewport().setAutoFillBackground(False)

    content.setAutoFillBackground(False)

    page.addWidget(scroll)

def expanding(widget: QWidget, *, vertical: str = "fixed") -> QWidget:

    """让控件在水平方向吃满、垂直方向按策略固定（表单行里最常用）。"""

    vertical_policy = (

        QSizePolicy.Policy.Fixed

        if vertical == "fixed"

        else QSizePolicy.Policy.Preferred

    )

    widget.setSizePolicy(QSizePolicy.Policy.Expanding, vertical_policy)

    return widget

def refresh_style(widget: QWidget) -> None:

    """动态属性变化后重新应用样式表。"""

    widget.style().unpolish(widget)

    widget.style().polish(widget)

    widget.update()

# ------------------------------------------------- 跨模块共享的控件

# 以下控件由设置对话框与动作/步骤编辑器共用，放在这里避免互相导入。

# 原实现位于 settings_dialog.py，逐字搬运。

class ElidedLabel(QLabel):

    """用省略号代替截断来显示过长文本的 QLabel。"""

    def __init__(self, text: str, parent: QWidget | None = None):

        super().__init__(text, parent)

        self._full_text = text

    def setText(self, text: str) -> None:

        """保存完整（未省略）文本，供下次 resize 时据此重新省略。"""

        self._full_text = str(text)

        super().setText(text)

    def minimumSizeHint(self) -> QSize:

        hint = super().minimumSizeHint()

        return QSize(80, hint.height())

    def resizeEvent(self, event) -> None:

        super().resizeEvent(event)

        metrics = QFontMetrics(self.font())

        # 调用基类 setText，确保完整文本不会被

        # 省略显示的文本覆盖。

        QLabel.setText(

            self,

            metrics.elidedText(

                self._full_text, Qt.TextElideMode.ElideRight, max(1, self.width())

            ),

        )

class ModelRowCard(QFrame):

    """可点击的模型行卡片；任意左键点击都会发出携带模型名的 ``clicked`` 信号。"""

    clicked = Signal(str)

    def __init__(self, model_name: str, parent: QWidget | None = None):

        super().__init__(parent)

        self._model_name = model_name

    def mouseReleaseEvent(self, event) -> None:

        super().mouseReleaseEvent(event)

        if event.button() == Qt.MouseButton.LeftButton:

            self.clicked.emit(self._model_name)

class SettingsComboBox(QComboBox):

    """统一下拉框：使用自绘弹层，滚轮不切换选项。"""

    class _PopupFrame(QFrame):

        """半透明弹层，圆角边框只绘制一次。"""

        def paintEvent(self, event) -> None:

            del event

            painter = QPainter(self)

            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            painter.setPen(QPen(QColor("#b9d3ca"), 1.0))

            painter.setBrush(QColor("#ffffff"))

            # 把描边控制在窗口内部，让半透明圆角保持透明，

            # 不会多出一个矩形边框。

            painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 7, 7)

    _POPUP_STYLE = _s.COMBO_POPUP_QSS

    def __init__(self, parent: QWidget | None = None) -> None:

        super().__init__(parent)

        self._settings_popup: QFrame | None = None

    def paintEvent(self, event) -> None:

        super().paintEvent(event)

        painter = QPainter(self)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        painter.setPen(

            QPen(

                QColor("#49615f"),

                1.6,

                Qt.PenStyle.SolidLine,

                Qt.PenCapStyle.RoundCap,

                Qt.PenJoinStyle.RoundJoin,

            )

        )

        center_x = self.width() - 16

        center_y = self.height() // 2

        painter.drawLine(center_x - 4, center_y - 2, center_x, center_y + 2)

        painter.drawLine(center_x, center_y + 2, center_x + 4, center_y - 2)

    def showPopup(self) -> None:

        self.hidePopup()

        popup = self._PopupFrame(

            self,

            Qt.WindowType.Popup

            | Qt.WindowType.FramelessWindowHint

            | Qt.WindowType.NoDropShadowWindowHint,

        )

        popup.setObjectName("settingsComboPopup")

        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        popup.setAutoFillBackground(False)

        popup.setStyleSheet(self._POPUP_STYLE)

        popup_layout = QVBoxLayout(popup)

        # 列表两侧保留相同的视觉留白；

        # 右侧多出的 1px 还避免滚动条贴到边框。

        popup_layout.setContentsMargins(5, 5, 6, 5)

        popup_layout.setSpacing(0)

        option_list = QListWidget(popup)

        option_list.setObjectName("settingsComboPopupList")

        option_list.setFrameShape(QFrame.Shape.NoFrame)

        option_list.setLineWidth(0)

        option_list.setMidLineWidth(0)

        option_list.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        option_list.viewport().setAttribute(

            Qt.WidgetAttribute.WA_TranslucentBackground, True

        )

        option_list.setAutoFillBackground(False)

        option_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

        option_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # 11 行 34px（0–10）正好组成一个完整的弹层。只有更长的

        # 列表才应变为可滚动；否则最后一项会被裁掉。

        has_overflow = self.count() > 11

        option_list.setVerticalScrollBarPolicy(

            Qt.ScrollBarPolicy.ScrollBarAsNeeded

            if has_overflow

            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff

        )

        for index in range(self.count()):

            item = QListWidgetItem(self.itemText(index))

            item.setData(Qt.ItemDataRole.UserRole, index)

            item.setSizeHint(QSize(0, 34))

            model_item = self.model().item(index)

            if model_item is not None and not model_item.isEnabled():

                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)

            option_list.addItem(item)

        option_list.setCurrentRow(max(0, self.currentIndex()))

        option_list.itemClicked.connect(self._accept_popup_item)

        option_list.itemActivated.connect(self._accept_popup_item)

        option_list.setFixedHeight(

            max(34, self.count() * 34) if not has_overflow else 350

        )

        popup_layout.addWidget(option_list)

        # 让弹层的每条边都与所属下拉框精确对齐。最小弹层宽度

        # 会让较短的控件意外地向右侧变宽。

        popup.setFixedWidth(max(1, self.width()))

        popup.setFixedHeight(option_list.height() + 10)

        origin = self.mapToGlobal(QPoint(0, 0))

        below = QPoint(origin.x(), origin.y() + self.height())

        screen = QApplication.screenAt(origin) or QApplication.primaryScreen()

        popup_position = below

        if screen is not None:

            available = screen.availableGeometry()

            parent_window = self.window()

            if parent_window is not None and parent_window.isVisible():

                # 通知必须保持在当前应用程序窗口内，

                # 而不是仅位于物理显示器内。

                available = available.intersected(parent_window.frameGeometry())

            x = max(

                available.left(), min(below.x(), available.right() - popup.width() + 1)

            )

            popup_height = popup.height()

            below_y = below.y()

            above_y = origin.y() - popup_height

            if below_y + popup_height <= available.bottom() + 1:

                y = below_y

            elif above_y >= available.top():

                y = above_y

            else:

                # 即使两侧空间都不足，也要让弹层完整可见

                # （例如在较低或缩放过的显示器上）。

                y = max(available.top(), available.bottom() - popup_height + 1)

            popup_position = QPoint(x, y)

        popup.move(popup_position)

        self._settings_popup = popup

        popup.show()

        # Qt.Popup 显示过程中 Windows 可能自行调整位置；

        # 因此在显示后再应用一次计算好的窗口内位置。

        popup.move(popup_position)

        option_list.setFocus()

    def hidePopup(self) -> None:

        popup = self._settings_popup

        self._settings_popup = None

        if popup is not None:

            popup.close()

            popup.deleteLater()

    def _accept_popup_item(self, item: QListWidgetItem) -> None:

        index = item.data(Qt.ItemDataRole.UserRole)

        if isinstance(index, int) and 0 <= index < self.count():

            self.setCurrentIndex(index)

            self.activated.emit(index)

        self.hidePopup()

    def wheelEvent(self, event) -> None:

        event.ignore()

class ExecutionCountComboBox(SettingsComboBox):

    """选择任务在“执行全部”批量运行中执行次数的下拉框。"""

    def __init__(self, parent: QWidget | None = None):

        super().__init__(parent)

        for count in range(11):

            self.addItem(f"{count} 次", count)

        self.setValue(1)

    def value(self) -> int:

        return int(self.currentData())

    def setValue(self, value: int) -> None:

        index = self.findData(min(10, max(0, int(value))))

        self.setCurrentIndex(max(0, index))

# ----------------------------------------------------------- 确认对话框

def confirm_dialog(parent: QWidget | None, title: str, text: str) -> bool:

    """显示取消在左、确认在右的确认框。"""

    return confirm(parent, title, text)
