"""``free_app`` 各 GUI 控件共享的 QSS 片段。

每个页面级样式表都通过 :data:`COMMON_CONTROLS_QSS` 获得标准控件
（输入框、微调框、文本框、下拉框、按钮与复选框）的统一外观，
使控件的几何尺寸与交互状态处处一致。这里还提供消息框、滚动条、
卡片标题与 OCR 反馈等更小的共享片段；各页面仍可在其基础上
追加布局/卡片/列表/语义色的例外规则。
"""

from __future__ import annotations

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
