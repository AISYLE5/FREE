"""Chinese, consistently styled message boxes for the desktop UI."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QMessageBox as _QtMessageBox

from . import styles as _s


class QMessageBox(_QtMessageBox):
    """QMessageBox with Chinese standard-button labels.

    The class keeps Qt's public API so existing callers and tests can still
    use ``QMessageBox.warning`` / ``information``.
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
