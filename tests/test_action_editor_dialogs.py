from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QListWidget

from free_app.action_editor_dialogs import (
    ActionEditorDialog,
    ActionEditorWidget,
    ActionListEditorWidget,
    CompoundEditorDialog,
)


class ActionEditorDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.application = QApplication.instance() or QApplication([])

    def test_collect_drops_unknown_primitive_keys(self) -> None:
        dialog = ActionEditorDialog(
            None,
            "编辑动作",
            {"type": "wait", "seconds": 2, "unknown_key": "x"},
            {},
        )
        try:
            data = dialog.collect()
            self.assertIsNotNone(data)
            self.assertEqual(data["seconds"], 2)
            self.assertNotIn("unknown_key", data)
        finally:
            dialog.deleteLater()

    def test_collect_rejects_missing_compound_reference(self) -> None:
        dialog = ActionEditorDialog(
            None,
            "编辑动作",
            {"type": "compound", "name": "ghost"},
            {},
        )
        try:
            with patch("free_app.action_editor_dialogs.QMessageBox.warning") as warning:
                self.assertIsNone(dialog.collect())
            warning.assert_called_once()
        finally:
            dialog.deleteLater()

    def test_compound_editor_collects_renamed_action(self) -> None:
        dialog = CompoundEditorDialog(
            None,
            "编辑复合动作",
            {"name": "old", "params": [], "steps": [{"type": "wait"}]},
        )
        try:
            dialog.name_edit.setText("new")
            data = dialog.collect()
            self.assertIsNotNone(data)
            self.assertEqual(data["name"], "new")
        finally:
            dialog.deleteLater()

    def test_click_coordinate_editor_collects_coordinates(self) -> None:
        dialog = ActionEditorDialog(
            None,
            "添加动作",
            {"type": "click", "locate": "coordinate"},
            {},
        )
        try:
            dialog._field_widgets["x"].setText("12")
            dialog._field_widgets["y"].setText("34")
            data = dialog.collect()
            self.assertIsNotNone(data)
            self.assertEqual(data["locate"], "coordinate")
            self.assertEqual(data["x"], 12)
            self.assertEqual(data["y"], 34)
        finally:
            dialog.deleteLater()

    def test_action_editor_offers_detect_and_if_primitives(self) -> None:
        dialog = ActionEditorDialog(
            None,
            "添加动作",
            {},
            {},
        )
        try:
            types = [dialog.type_combo.itemData(i) for i in range(dialog.type_combo.count())]
            self.assertIn("click", types)
            self.assertIn("detect", types)
            self.assertIn("if", types)
            self.assertIn("loop_until", types)
        finally:
            dialog.deleteLater()

    def test_launch_editor_has_wait_and_no_foreground_option(self) -> None:
        dialog = ActionEditorDialog(
            None,
            "添加动作",
            {"type": "launch"},
            {},
        )
        try:
            self.assertIn("wait_seconds", dialog._field_widgets)
            self.assertNotIn("assert_package", dialog._field_widgets)
        finally:
            dialog.deleteLater()

    def test_detect_editor_collects_ocr_fields(self) -> None:
        dialog = ActionEditorDialog(
            None,
            "添加动作",
            {"type": "detect", "locate": "ocr"},
            {},
        )
        try:
            dialog._field_widgets["texts"].setText("已领取, 领取")
            dialog._field_widgets["result_var"].setText("ocr_state")
            data = dialog.collect()
            self.assertIsNotNone(data)
            self.assertEqual(data["locate"], "ocr")
            self.assertEqual(data["texts"], ["已领取", "领取"])
            self.assertEqual(data["result_var"], "ocr_state")
        finally:
            dialog.deleteLater()

    def test_click_editor_uses_multi_target_labels_and_placeholders(self) -> None:
        dialog = ActionEditorDialog(
            None,
            "添加动作",
            {"type": "click", "locate": "ui"},
            {},
        )
        try:
            text_widget = dialog._field_widgets["text"]
            skip_widget = dialog._field_widgets["skip_if_texts"]
            self.assertIsInstance(text_widget, QLineEdit)
            self.assertIsInstance(skip_widget, QLineEdit)
            self.assertEqual(text_widget.placeholderText(), "例如：签到,领取")
            self.assertEqual(skip_widget.placeholderText(), "例如：已签到,已领取")
            text_widget.setText("签到，领取")
            skip_widget.setText("已签到, 已领取")
            data = dialog.collect()
            self.assertIsNotNone(data)
            self.assertEqual(data["text"], ["签到", "领取"])
            self.assertEqual(data["skip_if_texts"], ["已签到", "已领取"])
        finally:
            dialog.deleteLater()

    def test_if_editor_collects_nested_then_actions(self) -> None:
        dialog = ActionEditorDialog(
            None,
            "添加动作",
            {"type": "if", "var": "state", "equals": "领取"},
            {},
        )
        try:
            then_field = dialog._field_widgets["then"]
            then_field.set_steps(
                [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}]
            )
            data = dialog.collect()
            self.assertIsNotNone(data)
            self.assertEqual(data["var"], "state")
            self.assertEqual(data["equals"], "领取")
            self.assertEqual(
                data["then"],
                [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}],
            )
        finally:
            dialog.deleteLater()

    def test_embedded_if_editor_forwards_nested_steps_request(self) -> None:
        widget = ActionEditorWidget(None, {"type": "if"}, {})
        try:
            emitted: list[tuple[str, list[dict[str, object]]]] = []
            widget.nested_steps_edit_requested.connect(
                lambda key, steps: emitted.append((key, steps))
            )
            then_field = widget._field_widgets["then"]
            then_field.set_steps([{"type": "click", "locate": "coordinate", "x": 1, "y": 2}])
            then_field._edit_steps()
            self.assertEqual(
                emitted,
                [("then", [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}])],
            )
        finally:
            widget.deleteLater()

    def test_action_list_editor_syncs_drag_order(self) -> None:
        widget = ActionListEditorWidget(None, "成立时步骤")
        try:
            widget.load_steps(
                [
                    {"type": "click", "locate": "coordinate", "x": 1, "y": 2},
                    {"type": "wait", "seconds": 1},
                ]
            )
            widget.steps_list.item(0).setData(Qt.ItemDataRole.UserRole, 1)
            widget.steps_list.item(1).setData(Qt.ItemDataRole.UserRole, 0)
            widget._sync_steps_from_list()
            self.assertEqual(
                widget.get_steps(),
                [
                    {"type": "wait", "seconds": 1},
                    {"type": "click", "locate": "coordinate", "x": 1, "y": 2},
                ],
            )
            self.assertEqual(
                widget.steps_list.dragDropMode(),
                QListWidget.DragDropMode.InternalMove,
            )
        finally:
            widget.deleteLater()


if __name__ == "__main__":
    unittest.main()
