"""Shared QSS fragments used across the free_app GUI widgets.

Every page-level stylesheet consumes :data:`COMMON_CONTROLS_QSS` for the
shared appearance of standard controls (inputs, spin boxes, text edits,
combo boxes, buttons and checkboxes), so control geometry and interaction
states stay identical everywhere.  Smaller fragments remain available for
the shared message-box, scroll-bar, card-title and OCR-feedback rules;
each page may still add layout/card/list/semantic exceptions around them.
"""

from __future__ import annotations

# Vertical gradient shared by the green "save" / primary buttons.
GREEN_VGRAD = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a9385, stop:1 #137f73)"
GREEN_VGRAD_HOVER = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a9385, stop:1 #0e6e64)"
GREEN_DIAG = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1a9385, stop:1 #137f73)"
DARK_PRESSED_VGRAD = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #137f73, stop:1 #095b54)"

# --------------------------------------------------------------------------
# Form control box (QLineEdit / QSpinBox / QTextEdit / QPlainTextEdit).
# --------------------------------------------------------------------------
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

# --------------------------------------------------------------------------
# Combo box (base, dropdown arrow, popup item view).  Shared by all pages.
# --------------------------------------------------------------------------
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

# --------------------------------------------------------------------------
# Checkbox (base + indicator).  Shared from the action editors.
# --------------------------------------------------------------------------
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

# --------------------------------------------------------------------------
# Generic push button (base + semantic variants + interaction states).
# Shared by the action-editor dialogs and the task-manager widgets.
# --------------------------------------------------------------------------
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
    "            QPushButton#dangerButton, QPushButton#settingsModelAction[downloaded=\"true\"] {\n"
    "                color: #a34a42;\n"
    "                background: #fbeae8;\n"
    "                border-color: #e5b9b3;\n"
    "            }\n"
    "            QPushButton#dangerButton:hover, QPushButton#settingsModelAction[downloaded=\"true\"]:hover {\n"
    "                background: #f6d8d4;\n"
    "                border-color: #d88f87;\n"
    "            }\n"
    "            QPushButton#dangerButton:pressed, QPushButton#settingsModelAction[downloaded=\"true\"]:pressed {\n"
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

# One entry point for page-level stylesheets: base rules for every shared
# control, in a fixed order, so a page cannot add a second, slightly
# different base rule for the same public control.
COMMON_CONTROLS_QSS = (
    UNIFIED_FORM_CONTROLS_QSS
    + UNIFIED_COMBO_QSS
    + UNIFIED_CHECKBOX_QSS
    + UNIFIED_BUTTONS_QSS
)

# Shared look for the standalone editor dialogs (action / steps / compound):
# dialog background, paragraph labels, section titles and list styling.
DIALOG_QSS = (
    "\n"
    "            QDialog {\n"
    "                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f7faf9, stop:1 #f2f6f4);\n"
    "                color: #193331;\n"
    "            }\n"
    "            QDialog QLabel {\n"
    "                color: #193331;\n"
    "            }\n"
    "            QDialog QLabel#dialogSectionTitle {\n"
    "                color: #244340;\n"
    "                font-size: 13px;\n"
    "                font-weight: 700;\n"
    "            }\n"
    "            QDialog QListWidget {\n"
    "                background: #ffffff;\n"
    "                border: 1px solid #cbdcd6;\n"
    "                border-radius: 8px;\n"
    "                padding: 6px;\n"
    "            }\n"
    "            QDialog QListWidget::item {\n"
    "                padding: 8px 12px;\n"
    "                border-radius: 6px;\n"
    "                border: 1px solid transparent;\n"
    "                margin-bottom: 4px;\n"
    "            }\n"
    "            QDialog QListWidget::item:selected {\n"
    "                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #dff2ed, stop:1 #d0ebe4);\n"
    "                border: 1px solid #65b3a7;\n"
    "                color: #0f625b;\n"
    "            }\n"
    "            QDialog QListWidget::item:hover {\n"
    "                background: #edf7f4;\n"
    "                border: 1px solid #e0e9e6;\n"
    "            }\n"
)

# Slim scrollbar shared by the settings page and all application views.
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

# Keep native message boxes readable when a parent widget uses transparent
# backgrounds.  Without these rules, Windows can expose a black dialog body
# while retaining the application's dark text color.
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

# --------------------------------------------------------------------------
# Small panel labels.  Shared by several dialogs/widgets.
# --------------------------------------------------------------------------
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

# SettingsComboBox draws its own popup rather than using Qt's native popup.
# Keep that popup's public dropdown styling here as well.
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
