"""为桌面 UI 提供风格统一的中文化消息框。"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QMessageBox as _QtMessageBox

from . import styles as _s


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
