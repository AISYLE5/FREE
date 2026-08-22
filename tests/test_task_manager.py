from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from free_app.action_editor_dialogs import ActionEditorDialog, CompoundEditorDialog
from free_app.config import expand_action_for_run
from free_app.message_box import QMessageBox
from free_app.task_manager import TaskManagerWidget, UiTreeDumpWidget
from free_app.ui_automation import Bounds, UiNode, UiSnapshot


class TaskEditorTests(unittest.TestCase):
    @staticmethod
    def _write_task_file(base: Path, data: dict) -> Path:
        tasks_directory = base / "config" / "tasks"
        tasks_directory.mkdir(parents=True, exist_ok=True)
        task_path = tasks_directory / f"{data['id']}.json"
        task_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return task_path

    def _make_widget(self, base: Path) -> TaskManagerWidget:
        application = QApplication.instance() or QApplication([])
        config_directory = base / "config"
        config_directory.mkdir(exist_ok=True)
        settings_path = config_directory / "settings.json"
        if not settings_path.exists():
            settings_path.write_text(
                json.dumps({"qq_group_name": "测试群"}, ensure_ascii=False),
                encoding="utf-8",
            )
        return TaskManagerWidget(settings_path, base_directory=base)

    def test_task_editor_lists_tasks_and_loads_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 2}],
                },
            )
            dialog = self._make_widget(base)
            try:
                self.assertEqual(dialog.task_list.count(), 1)
                self.assertEqual(dialog.task_id_edit.text(), "demo")
                self.assertEqual(dialog.task_name_edit.text(), "Demo")
                self.assertEqual(len(dialog._actions_buffer), 1)
            finally:
                dialog.deleteLater()

    def test_reload_preserves_task_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(base, {"id": "task_a", "name": "A", "package": "a", "actions": [{"type": "wait", "seconds": 1}]})
            self._write_task_file(base, {"id": "task_b", "name": "B", "package": "b", "actions": [{"type": "wait", "seconds": 1}]})
            (base / "config" / "settings.json").write_text(json.dumps({"task_order": ["task_b", "task_a"]}), encoding="utf-8")
            dialog = self._make_widget(base)
            try:
                self.assertIn("task_b", dialog.task_list.item(0).text())
            finally:
                dialog.deleteLater()

    def test_save_new_task_persists_task_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dialog = self._make_widget(base)
            try:
                dialog._new_task()
                dialog.task_id_edit.setText("new_task")
                dialog.task_name_edit.setText("New")
                dialog.task_package_edit.setText("pkg")
                dialog._actions_buffer = [{"type": "wait", "seconds": 1}]
                dialog._save_task()
                settings = json.loads((base / "config" / "settings.json").read_text(encoding="utf-8"))
                self.assertEqual(settings["task_order"], ["new_task"])
            finally:
                dialog.deleteLater()

    def test_rename_task_replaces_order_and_recycles_old_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            old_path = self._write_task_file(base, {"id": "old", "name": "Old", "package": "pkg", "actions": [{"type": "wait", "seconds": 1}]})
            (base / "config" / "settings.json").write_text(json.dumps({"task_order": ["before", "old", "after"]}), encoding="utf-8")
            dialog = self._make_widget(base)
            try:
                dialog.task_id_edit.setText("new")
                dialog.task_name_edit.setText("New")
                with patch("free_app.task_manager.send_to_recycle_bin", side_effect=lambda item: item.unlink()) as recycle:
                    dialog._save_task()
                recycle.assert_called_once_with(old_path)
                self.assertFalse(old_path.exists())
                settings = json.loads((base / "config" / "settings.json").read_text(encoding="utf-8"))
                self.assertEqual(settings["task_order"], ["before", "new", "after"])
            finally:
                dialog.deleteLater()

    def test_duplicate_task_appends_copy_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog._duplicate_task()
                self.assertEqual(dialog.task_id_edit.text(), "demo_copy")
                self.assertEqual(dialog.task_name_edit.text(), "Demo")
                self.assertIn("demo_copy", dialog._tasks)
                self.assertEqual(
                    dialog._tasks["demo_copy"]["actions"],
                    [{"type": "wait", "seconds": 1}],
                )
            finally:
                dialog.deleteLater()

    def test_new_task_rejects_existing_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog._new_task()
                dialog.task_id_edit.setText("demo")
                dialog.task_name_edit.setText("Demo")
                dialog.task_package_edit.setText("demo.package")
                dialog._actions_buffer = [{"type": "wait", "seconds": 1}]
                with patch("free_app.task_manager.QMessageBox.warning") as warning:
                    dialog._save_task()
                warning.assert_called_once()
                self.assertIn("已存在", warning.call_args.args[2])
            finally:
                dialog.deleteLater()

    def test_new_task_saves_valid_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog._new_task()
                dialog.task_id_edit.setText("demo2")
                dialog.task_name_edit.setText("Demo 2")
                dialog.task_package_edit.setText("demo.package")
                dialog._actions_buffer = [{"type": "wait", "seconds": 1}]
                with patch("free_app.task_manager.QMessageBox.warning") as warning:
                    dialog._save_task()
                warning.assert_not_called()
                task_path = base / "config" / "tasks" / "demo2.json"
                self.assertTrue(task_path.exists())
                saved = json.loads(task_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["name"], "Demo 2")
                self.assertEqual(saved["actions"], [{"type": "wait", "seconds": 1}])
                self.assertEqual(dialog.task_id_edit.text(), "demo2")
            finally:
                dialog.deleteLater()

    def test_delete_task_recycles_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            task_path = self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            dialog = self._make_widget(base)
            try:
                with patch("free_app.task_manager.confirm_dialog", return_value=True), patch(
                    "free_app.task_manager.send_to_recycle_bin"
                ) as recycle:
                    dialog._delete_task()
                recycle.assert_called_once_with(task_path)
                self.assertNotIn("demo", dialog._tasks)
            finally:
                dialog.deleteLater()

    def test_delete_task_permanent_removes_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            task_path = self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            dialog = self._make_widget(base)
            try:
                permanent_index = dialog.cleanup_mode_combo.findData("permanent")
                dialog.cleanup_mode_combo.setCurrentIndex(permanent_index)
                with patch("free_app.task_manager.confirm_dialog", return_value=True):
                    dialog._delete_task()
                self.assertFalse(task_path.exists())
                self.assertNotIn("demo", dialog._tasks)
            finally:
                dialog.deleteLater()

    def test_save_compound_writes_actions_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            dialog = self._make_widget(base)
            try:
                data = {
                    "name": "demo_action",
                    "description": "",
                    "params": [],
                    "steps": [{"type": "wait", "seconds": 1}],
                }
                with patch("free_app.task_manager.QMessageBox.warning") as warning:
                    dialog._save_compound(data)
                warning.assert_not_called()
                action_path = base / "config" / "actions" / "demo_action.json"
                self.assertTrue(action_path.exists())
                self.assertIn("demo_action", dialog._compound_library)
            finally:
                dialog.deleteLater()

    def test_action_editor_offers_primitive_presets(self) -> None:
        application = QApplication.instance() or QApplication([])
        dialog = ActionEditorDialog(None, "添加动作", {}, {})
        try:
            types = [dialog.type_combo.itemData(i) for i in range(dialog.type_combo.count())]
            for expected in (
                "click",
                "wait",
                "back",
                "detect",
                "if",
                "loop_until",
                "compound",
            ):
                self.assertIn(expected, types)
        finally:
            dialog.deleteLater()

    def test_action_editor_collects_click_text_form(self) -> None:
        application = QApplication.instance() or QApplication([])
        dialog = ActionEditorDialog(None, "添加动作", {}, {})
        try:
            self.assertEqual(dialog.type_combo.currentData(), "click")
            self.assertEqual(dialog._field_widgets["locate"].currentData(), "ui")
            dialog._field_widgets["text"].setText("签到")
            dialog._field_widgets["timeout_seconds"].setText("20")
            data = dialog.collect()
            self.assertEqual(
                data,
                {
                    "type": "click",
                    "locate": "ui",
                    "target": "text",
                    "text": ["签到"],
                    "match_mode": "exact",
                    "timeout_seconds": 20,
                },
            )
        finally:
            dialog.deleteLater()

    def test_action_editor_validates_required_coordinates(self) -> None:
        application = QApplication.instance() or QApplication([])
        dialog = ActionEditorDialog(None, "添加动作", {"type": "click", "locate": "coordinate"}, {})
        try:
            with patch("free_app.action_editor_dialogs.QMessageBox.warning") as warning:
                data = dialog.collect()
            warning.assert_called_once()
            self.assertIsNone(data)
        finally:
            dialog.deleteLater()

    def test_action_editor_loads_existing_coordinate_click(self) -> None:
        application = QApplication.instance() or QApplication([])
        dialog = ActionEditorDialog(
            None,
            "编辑动作",
            {"type": "click", "locate": "coordinate", "x": 540, "y": 960},
            {},
        )
        try:
            self.assertEqual(dialog._field_widgets["x"].text(), "540")
            self.assertEqual(dialog._field_widgets["y"].text(), "960")
            data = dialog.collect()
            self.assertEqual(
                data,
                {"type": "click", "locate": "coordinate", "x": 540, "y": 960},
            )
        finally:
            dialog.deleteLater()

    def test_action_editor_compound_selects_name(self) -> None:
        application = QApplication.instance() or QApplication([])
        library = {
            "share_group": {
                "name": "share_group",
                "description": "分享到群",
                "steps": [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}],
            }
        }
        dialog = ActionEditorDialog(None, "添加动作", {}, library)
        try:
            index = dialog.type_combo.findData("compound")
            dialog.type_combo.setCurrentIndex(index)
            self.assertEqual(dialog._field_widgets["name"].currentData(), "share_group")
            self.assertNotIn("group_name", dialog._field_widgets)
            data = dialog.collect()
            self.assertEqual(
                data,
                {
                    "type": "compound",
                    "name": "share_group",
                },
            )
        finally:
            dialog.deleteLater()

    def test_compound_editor_collects_form(self) -> None:
        application = QApplication.instance() or QApplication([])
        dialog = CompoundEditorDialog(
            None, "新建复合动作", {"name": "", "description": "", "steps": []}
        )
        try:
            dialog.name_edit.setText("my_share")
            dialog._steps.append(
                {"type": "click", "locate": "coordinate", "x": 1, "y": 2}
            )
            dialog._refresh_steps()
            data = dialog.collect()
            self.assertEqual(
                data,
                {
                    "name": "my_share",
                    "steps": [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}],
                },
            )
        finally:
            dialog.deleteLater()

    def test_compound_editor_rejects_empty_steps(self) -> None:
        application = QApplication.instance() or QApplication([])
        dialog = CompoundEditorDialog(
            None, "新建复合动作", {"name": "", "description": "", "steps": []}
        )
        try:
            dialog.name_edit.setText("my_share")
            with patch("free_app.action_editor_dialogs.QMessageBox.warning") as warning:
                data = dialog.collect()
            warning.assert_called_once()
            self.assertIsNone(data)
        finally:
            dialog.deleteLater()

    def test_add_action_opens_embedded_editor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog._add_action()
                self.assertEqual(dialog._embedded_editor_mode, "task_action")
                self.assertEqual(dialog._embedded_editor_index, -1)
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.embedded_action_editor_panel,
                )
            finally:
                dialog.deleteLater()

    def test_new_task_can_add_action_from_unified_operation_bar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dialog = self._make_widget(base)
            try:
                dialog._new_task()
                self.assertEqual(dialog._current_operation_context(), "action")
                dialog._add_current()
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.embedded_action_editor_panel,
                )
                self.assertEqual(dialog._embedded_editor_mode, "task_action")
                self.assertEqual(dialog._embedded_editor_index, -1)
            finally:
                dialog.deleteLater()

    def test_new_compound_can_add_step_from_unified_operation_bar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dialog = self._make_widget(base)
            try:
                dialog.left_tabs.setCurrentIndex(1)
                dialog._new_compound()
                self.assertEqual(dialog._current_operation_context(), "step")
                dialog._add_current()
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.embedded_action_editor_panel,
                )
                self.assertEqual(dialog._embedded_editor_mode, "compound_step")
                self.assertEqual(dialog._embedded_editor_index, -1)
            finally:
                dialog.deleteLater()

    def test_standard_message_box_uses_chinese_ok_label(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dialog = self._make_widget(base)
            try:
                labels: list[str] = []

                def fake_exec(message_box) -> int:
                    labels.extend(button.text() for button in message_box.buttons())
                    message_box.buttons()[0].click()
                    return 0

                with patch.object(QMessageBox, "exec", new=fake_exec):
                    result = QMessageBox.warning(dialog, "无法保存", "目标文本不能为空")
                self.assertEqual(labels, ["确定"])
                self.assertEqual(result, QMessageBox.StandardButton.Ok)
            finally:
                dialog.deleteLater()

    def test_clicking_current_task_returns_from_action_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog.actions_list.setCurrentRow(0)
                self.assertEqual(dialog._current_operation_context(), "action")
                dialog._on_task_item_clicked(dialog.task_list.currentItem())
                self.assertEqual(dialog.actions_list.currentRow(), -1)
                self.assertEqual(dialog._current_operation_context(), "task")
            finally:
                dialog.deleteLater()

    def test_switching_task_after_action_selection_loads_new_task_editor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "task_a",
                    "name": "A",
                    "package": "a.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            self._write_task_file(
                base,
                {
                    "id": "task_b",
                    "name": "B",
                    "package": "b.package",
                    "actions": [{"type": "back"}],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog.actions_list.setCurrentRow(0)
                dialog.task_list.setCurrentRow(1)
                self.assertEqual(dialog._selected_task, "task_b")
                self.assertEqual(dialog.task_name_edit.text(), "B")
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.editor_stack.widget(0),
                )
                self.assertEqual(dialog.actions_list.currentRow(), -1)
            finally:
                dialog.deleteLater()

    def test_unified_operation_bar_switches_between_task_and_action_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            dialog = self._make_widget(base)
            try:
                self.assertEqual(dialog.operation_context_label.text(), "任务操作")
                self.assertEqual(dialog.add_button.text(), "添加")
                self.assertEqual(dialog.copy_button.text(), "复制")
                self.assertEqual(dialog.delete_button.text(), "删除")
                self.assertTrue(dialog.run_action_button.isHidden())

                dialog.actions_list.setCurrentRow(0)
                self.assertEqual(dialog.operation_context_label.text(), "动作操作")
                self.assertFalse(dialog.run_action_button.isHidden())
                self.assertTrue(dialog.copy_button.isEnabled())
                self.assertTrue(dialog.delete_button.isEnabled())
                self.assertEqual(dialog.save_button.text(), "保存")
            finally:
                dialog.deleteLater()

    def test_duplicate_action_inserts_copy_after_selected_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [
                        {"type": "wait", "seconds": 1},
                        {"type": "back"},
                    ],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog.actions_list.setCurrentRow(0)
                dialog._duplicate_current()
                self.assertEqual(
                    dialog._actions_buffer,
                    [
                        {"type": "wait", "seconds": 1},
                        {"type": "wait", "seconds": 1},
                        {"type": "back"},
                    ],
                )
                self.assertEqual(dialog.actions_list.currentRow(), 1)
                self.assertTrue(dialog._dirty)
            finally:
                dialog.deleteLater()

    def test_switching_task_with_unsaved_changes_can_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "task_a",
                    "name": "A",
                    "package": "a.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            self._write_task_file(
                base,
                {
                    "id": "task_b",
                    "name": "B",
                    "package": "b.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog.task_name_edit.setText("A changed")
                with patch(
                    "free_app.task_manager.TaskManagerWidget._ask_unsaved_changes",
                    return_value="cancel",
                ):
                    dialog.task_list.setCurrentRow(1)
                self.assertEqual(dialog.task_list.currentRow(), 0)
                self.assertEqual(dialog._selected_task, "task_a")
                self.assertEqual(dialog.task_name_edit.text(), "A changed")
            finally:
                dialog.deleteLater()

    def test_go_back_cancel_in_embedded_editor_stays_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 2}],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog._show_embedded_editor(
                    "task_action",
                    index=0,
                    initial={"type": "click"},
                    return_panel=dialog.editor_stack.widget(0),
                )
                dialog._embedded_editor_original = {"type": "click"}
                dialog.embedded_action_editor.load_data({"type": "wait", "seconds": 3})
                with patch(
                    "free_app.task_manager.TaskManagerWidget._ask_unsaved_changes",
                    return_value="cancel",
                ):
                    self.assertTrue(dialog.go_back())
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.embedded_action_editor_panel,
                )
            finally:
                dialog.deleteLater()

    def test_go_back_discard_in_embedded_editor_returns_to_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 2}],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog._show_embedded_editor(
                    "task_action",
                    index=0,
                    initial={"type": "click"},
                    return_panel=dialog.editor_stack.widget(0),
                )
                dialog._embedded_editor_original = {"type": "click"}
                dialog.embedded_action_editor.load_data({"type": "wait", "seconds": 3})
                with patch(
                    "free_app.task_manager.TaskManagerWidget._ask_unsaved_changes",
                    return_value="discard",
                ):
                    self.assertTrue(dialog.go_back())
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.editor_stack.widget(0),
                )
            finally:
                dialog.deleteLater()

    def test_go_back_cancel_with_dirty_base_editor_stays_on_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 2}],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog.task_name_edit.setText("changed")
                with patch(
                    "free_app.task_manager.TaskManagerWidget._ask_unsaved_changes",
                    return_value="cancel",
                ):
                    self.assertTrue(dialog.go_back())
                self.assertEqual(dialog.task_name_edit.text(), "changed")
                self.assertTrue(dialog._dirty)
            finally:
                dialog.deleteLater()

    def test_go_back_discard_with_dirty_base_editor_exits_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 2}],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog.task_name_edit.setText("changed")
                with patch(
                    "free_app.task_manager.TaskManagerWidget._ask_unsaved_changes",
                    return_value="discard",
                ):
                    self.assertFalse(dialog.go_back())
            finally:
                dialog.deleteLater()

    def test_task_rename_preserves_description_and_execution_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "old",
                    "name": "Old",
                    "package": "demo.package",
                    "description": "保留说明",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            (base / "config" / "settings.json").write_text(
                json.dumps({"task_execution_counts": {"old": 4}}),
                encoding="utf-8",
            )
            dialog = self._make_widget(base)
            try:
                dialog.task_id_edit.setText("new")
                with patch(
                    "free_app.task_manager.send_to_recycle_bin",
                    side_effect=lambda item: item.unlink(),
                ):
                    self.assertTrue(dialog._save_task())
                saved = json.loads(
                    (base / "config" / "tasks" / "new.json").read_text(encoding="utf-8")
                )
                settings = json.loads(
                    (base / "config" / "settings.json").read_text(encoding="utf-8")
                )
                self.assertEqual(saved["description"], "保留说明")
                self.assertEqual(settings["task_execution_counts"], {"new": 4})
            finally:
                dialog.deleteLater()

    def test_compound_rename_updates_task_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "compound", "name": "old"}],
                },
            )
            action_directory = base / "config" / "actions"
            action_directory.mkdir(parents=True, exist_ok=True)
            (action_directory / "old.json").write_text(
                json.dumps(
                    {
                        "name": "old",
                        "description": "说明",
                        "params": [],
                        "steps": [{"type": "wait", "seconds": 1}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            dialog = self._make_widget(base)
            try:
                dialog.left_tabs.setCurrentIndex(1)
                dialog.compound_name_edit.setText("new")
                with patch(
                    "free_app.task_manager.send_to_recycle_bin",
                    side_effect=lambda item: item.unlink(),
                ):
                    self.assertTrue(dialog._save_compound_from_editor())
                saved_task = json.loads(
                    (base / "config" / "tasks" / "demo.json").read_text(encoding="utf-8")
                )
                self.assertEqual(saved_task["actions"][0]["name"], "new")
                self.assertIn("new", dialog._compound_library)
                self.assertNotIn("old", dialog._compound_library)
            finally:
                dialog.deleteLater()

    def test_view_task_json_opens_viewer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog._view_task_json()
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.embedded_json_viewer_panel,
                )
                self.assertIn('"id": "demo"', dialog.embedded_json_viewer.editor.toPlainText())
            finally:
                dialog.deleteLater()

    def test_branch_steps_open_in_embedded_panel_and_save_back_to_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [
                        {
                            "type": "if",
                            "var": "state",
                            "equals": "1",
                            "then": [],
                            "else": [],
                        }
                    ],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog.actions_list.setCurrentRow(0)
                dialog._edit_action()
                dialog.embedded_action_editor.nested_steps_edit_requested.emit(
                    "then",
                    [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}],
                )
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.embedded_branch_steps_editor_panel,
                )
                self.assertEqual(
                    dialog.embedded_branch_steps_editor.steps_list.count(),
                    1,
                )
                dialog._on_branch_steps_saved(
                    [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}]
                )
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.embedded_action_editor_panel,
                )
                then_field = dialog.embedded_action_editor._field_widgets["then"]
                self.assertEqual(
                    then_field.get_steps(),
                    [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}],
                )
            finally:
                dialog.deleteLater()

    def test_branch_step_add_uses_embedded_action_editor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [
                        {
                            "type": "if",
                            "var": "state",
                            "equals": "1",
                            "then": [],
                            "else": [],
                        }
                    ],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog.actions_list.setCurrentRow(0)
                dialog._edit_action()
                dialog._open_branch_steps_editor("then", [])
                dialog._add_branch_step()
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.embedded_action_editor_panel,
                )
                self.assertEqual(dialog._embedded_editor_mode, "branch_step")
                self.assertEqual(dialog._embedded_editor_index, -1)
                dialog._on_embedded_editor_saved(
                    {"type": "click", "locate": "coordinate", "x": 5, "y": 6}
                )
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.embedded_branch_steps_editor_panel,
                )
                self.assertEqual(
                    dialog.embedded_branch_steps_editor.steps_list.count(),
                    1,
                )
                dialog._on_branch_steps_saved(
                    dialog.embedded_branch_steps_editor.get_steps()
                )
                then_field = dialog.embedded_action_editor._field_widgets["then"]
                self.assertEqual(
                    then_field.get_steps(),
                    [{"type": "click", "locate": "coordinate", "x": 5, "y": 6}],
                )
            finally:
                dialog.deleteLater()

    def test_go_back_from_action_editor_saves_changes_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog.actions_list.setCurrentRow(0)
                dialog._edit_action()
                dialog.embedded_action_editor._field_widgets["seconds"].setText("5")
                with patch(
                    "free_app.task_manager.TaskManagerWidget._ask_unsaved_changes",
                    return_value="save",
                ):
                    self.assertTrue(dialog.go_back())
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.editor_stack.widget(dialog.left_tabs.currentIndex()),
                )
                self.assertEqual(
                    dialog._actions_buffer[0],
                    {"type": "wait", "seconds": 5},
                )
            finally:
                dialog.deleteLater()

    def test_go_back_rejects_invalid_input_and_stays_in_editor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog.actions_list.setCurrentRow(0)
                dialog._edit_action()
                dialog.embedded_action_editor._field_widgets["seconds"].setText("abc")
                with patch(
                    "free_app.task_manager.TaskManagerWidget._ask_unsaved_changes",
                    return_value="save",
                ), patch("free_app.task_manager.QMessageBox.warning") as warning:
                    self.assertTrue(dialog.go_back())
                warning.assert_called_once()
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.embedded_action_editor_panel,
                )
                self.assertEqual(
                    dialog._actions_buffer[0],
                    {"type": "wait", "seconds": 1},
                )
            finally:
                dialog.deleteLater()

    def test_go_back_from_branch_steps_saves_to_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [
                        {
                            "type": "if",
                            "var": "state",
                            "equals": "1",
                            "then": [],
                            "else": [],
                        }
                    ],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog.actions_list.setCurrentRow(0)
                dialog._edit_action()
                dialog._open_branch_steps_editor(
                    "then",
                    [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}],
                )
                self.assertTrue(dialog.go_back())
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.embedded_action_editor_panel,
                )
                then_field = dialog.embedded_action_editor._field_widgets["then"]
                self.assertEqual(
                    then_field.get_steps(),
                    [{"type": "click", "locate": "coordinate", "x": 1, "y": 2}],
                )
            finally:
                dialog.deleteLater()

    def test_go_back_from_json_opened_inside_action_editor_returns_to_editor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._write_task_file(
                base,
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            dialog = self._make_widget(base)
            try:
                dialog.actions_list.setCurrentRow(0)
                dialog._edit_action()
                dialog._view_task_json()
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.embedded_json_viewer_panel,
                )
                self.assertTrue(dialog.go_back())
                self.assertIs(
                    dialog.editor_stack.currentWidget(),
                    dialog.embedded_action_editor_panel,
                )
            finally:
                dialog.deleteLater()

    def test_cleanup_mode_writes_back_to_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dialog = self._make_widget(base)
            try:
                permanent_index = dialog.cleanup_mode_combo.findData("permanent")
                dialog.cleanup_mode_combo.setCurrentIndex(permanent_index)
                settings = json.loads(
                    (base / "config" / "settings.json").read_text(encoding="utf-8")
                )
                self.assertEqual(settings["cleanup_mode"], "permanent")
            finally:
                dialog.deleteLater()

    def test_save_task_emits_tasks_changed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dialog = self._make_widget(base)
            try:
                dialog._new_task()
                dialog.task_id_edit.setText("brand_new")
                dialog.task_name_edit.setText("New Task")
                dialog.task_package_edit.setText("demo.package")
                dialog._actions_buffer = [{"type": "wait", "seconds": 1}]
                emitted = []
                dialog.tasks_changed.connect(lambda: emitted.append(True))
                with patch("free_app.task_manager.QMessageBox.warning") as warning:
                    dialog._save_task()
                warning.assert_not_called()
                self.assertEqual(emitted, [True])
                self.assertTrue((base / "config" / "tasks" / "brand_new.json").exists())
            finally:
                dialog.deleteLater()



    def test_new_compound_rejects_duplicate_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            action_directory = base / "config" / "actions"
            action_directory.mkdir(parents=True)
            action_path = action_directory / "existing.json"
            original = {"name": "existing", "description": "old", "params": [], "steps": [{"type": "wait", "seconds": 1}]}
            action_path.write_text(json.dumps(original), encoding="utf-8")
            dialog = self._make_widget(base)
            try:
                with patch("free_app.task_manager.QMessageBox.warning") as warning:
                    dialog._save_compound({"name": "existing", "description": "new", "params": [], "steps": [{"type": "wait", "seconds": 2}]}, previous_name="")
                warning.assert_called_once()
                self.assertEqual(json.loads(action_path.read_text(encoding="utf-8")), original)
            finally:
                dialog.deleteLater()

    def test_rename_compound_recycles_old_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            action_directory = base / "config" / "actions"
            action_directory.mkdir(parents=True)
            old_path = action_directory / "old.json"
            old_path.write_text(json.dumps({"name": "old", "description": "", "params": [], "steps": [{"type": "wait", "seconds": 1}]}), encoding="utf-8")
            dialog = self._make_widget(base)
            try:
                dialog.compound_list.setCurrentRow(0)
                self.assertEqual(dialog.compound_name_edit.text(), "old")
                dialog.compound_name_edit.setText("new")
                with patch("free_app.task_manager.send_to_recycle_bin", side_effect=lambda item: item.unlink()) as recycle:
                    dialog._save_compound_from_editor()
                recycle.assert_called_once_with(old_path)
                self.assertFalse(old_path.exists())
                self.assertNotIn("old", dialog._compound_library)
                self.assertIn("new", dialog._compound_library)
            finally:
                dialog.deleteLater()

    def test_compound_selection_loads_editor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            action_directory = base / "config" / "actions"
            action_directory.mkdir(parents=True)
            (action_directory / "share.json").write_text(
                json.dumps(
                    {
                        "name": "share",
                        "description": "分享到群",
                        "params": ["group_name"],
                        "steps": [{"type": "wait", "seconds": 1}, {"type": "back", "times": 1}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            dialog = self._make_widget(base)
            try:
                self.assertEqual(dialog.compound_name_edit.text(), "share")
                self.assertEqual(len(dialog._steps_buffer), 2)
                self.assertEqual(dialog.compound_steps_list.count(), 2)
            finally:
                dialog.deleteLater()


class UiTreeDumpWidgetTests(unittest.TestCase):
    @staticmethod
    def _snapshot() -> UiSnapshot:
        return UiSnapshot(
            [
                UiNode(
                    text="领取",
                    content_description="",
                    resource_id="com.example:id/claim",
                    class_name="android.widget.Button",
                    bounds=Bounds(100, 200, 300, 400),
                    clickable=True,
                    enabled=True,
                    visible=True,
                )
            ]
        )

    @staticmethod
    def _make_widget(base: Path) -> TaskManagerWidget:
        application = QApplication.instance() or QApplication([])
        config_directory = base / "config"
        config_directory.mkdir(exist_ok=True)
        settings_path = config_directory / "settings.json"
        if not settings_path.exists():
            settings_path.write_text(
                json.dumps({"qq_group_name": "测试群"}, ensure_ascii=False),
                encoding="utf-8",
            )
        return TaskManagerWidget(settings_path, base_directory=base)

    def test_try_click_button_emits_selected_node_center(self) -> None:
        application = QApplication.instance() or QApplication([])
        widget = UiTreeDumpWidget()
        try:
            widget.load_snapshot(self._snapshot())
            self.assertTrue(widget.try_click_button.isEnabled())
            widget.tree.setCurrentItem(widget.tree.topLevelItem(0))
            emitted = []
            widget.try_click_requested.connect(
                lambda x, y, label: emitted.append((x, y, label))
            )
            widget._try_click()
            self.assertEqual(emitted, [(200, 300, "领取")])
        finally:
            widget.deleteLater()

    def test_search_filter_supports_fuzzy_and_exact_text_modes(self) -> None:
        application = QApplication.instance() or QApplication([])
        snapshot = UiSnapshot(
            [
                UiNode(
                    text="领取奖励",
                    content_description="",
                    resource_id="",
                    class_name="android.widget.Button",
                    bounds=Bounds(0, 0, 100, 80),
                    clickable=True,
                    enabled=True,
                    visible=True,
                ),
                UiNode(
                    text="领取",
                    content_description="",
                    resource_id="",
                    class_name="android.widget.Button",
                    bounds=Bounds(100, 0, 200, 80),
                    clickable=True,
                    enabled=True,
                    visible=True,
                ),
                UiNode(
                    text="其他",
                    content_description="",
                    resource_id="",
                    class_name="android.widget.Button",
                    bounds=Bounds(200, 0, 300, 80),
                    clickable=True,
                    enabled=True,
                    visible=True,
                ),
            ]
        )
        widget = UiTreeDumpWidget()
        try:
            widget.load_snapshot(snapshot)
            widget.search_edit.setText("领取")
            visible = [not item.isHidden() for item in widget._all_items]
            self.assertEqual(visible, [True, True, False])

            exact_index = widget.search_mode_combo.findData("exact")
            widget.search_mode_combo.setCurrentIndex(exact_index)
            visible = [not item.isHidden() for item in widget._all_items]
            self.assertEqual(visible, [False, True, False])

            widget.search_edit.setText("领取%")
            visible = [not item.isHidden() for item in widget._all_items]
            self.assertEqual(visible, [False, False, False])

            fuzzy_index = widget.search_mode_combo.findData("fuzzy")
            widget.search_mode_combo.setCurrentIndex(fuzzy_index)
            visible = [not item.isHidden() for item in widget._all_items]
            self.assertEqual(visible, [True, True, False])
        finally:
            widget.deleteLater()

    def test_task_manager_try_click_taps_via_dump_adb(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            manager = self._make_widget(base)
            try:
                taps: list[tuple[int, int]] = []

                class FakeAdb:
                    def tap(self, x: int, y: int) -> None:
                        taps.append((x, y))

                manager._ui_tree_adb = FakeAdb()
                manager._on_embedded_ui_tree_try_click_requested(200, 300, "领取")
                self.assertEqual(taps, [(200, 300)])
            finally:
                manager.deleteLater()

    def test_task_manager_try_click_without_adb_warns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            manager = self._make_widget(base)
            try:
                with patch("free_app.task_manager.QMessageBox.warning") as warning:
                    manager._on_embedded_ui_tree_try_click_requested(10, 20, "")
                warning.assert_called_once()
            finally:
                manager.deleteLater()

    def test_copy_package_copies_foreground_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            manager = self._make_widget(base)
            try:
                class FakeAdb:
                    def current_package(self) -> str:
                        return "tv.danmaku.bili"

                feedback: list[str] = []
                manager.feedback_requested.connect(feedback.append)
                with patch.object(
                    TaskManagerWidget,
                    "_connect_to_mumu",
                    return_value=FakeAdb(),
                ):
                    manager._on_copy_package_clicked()
                self.assertEqual(QApplication.clipboard().text(), "tv.danmaku.bili")
                self.assertEqual(feedback, ["tv.danmaku.bili"])
            finally:
                manager.deleteLater()

    def test_copy_package_handles_missing_foreground_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            manager = self._make_widget(base)
            try:
                class FakeAdb:
                    def current_package(self) -> str | None:
                        return None

                with patch.object(
                    TaskManagerWidget,
                    "_connect_to_mumu",
                    return_value=FakeAdb(),
                ), patch.object(manager, "_show_timed_warning") as warning:
                    manager._on_copy_package_clicked()
                warning.assert_called_once_with(
                    "获取包名失败",
                    "未识别到前台应用包名。",
                )
            finally:
                manager.deleteLater()

    def test_insert_click_uses_selected_text_mode(self) -> None:
        application = QApplication.instance() or QApplication([])
        widget = UiTreeDumpWidget()
        try:
            widget.load_snapshot(self._snapshot())
            widget.tree.setCurrentItem(widget.tree.topLevelItem(0))
            widget.insert_mode_combo.setCurrentIndex(
                widget.insert_mode_combo.findData("text")
            )
            emitted: list[dict[str, object]] = []
            widget.action_inserted.connect(emitted.append)
            widget._insert_click()
            self.assertEqual(
                emitted,
                [
                    {
                        "type": "click",
                        "locate": "ui",
                        "target": "text",
                        "text": "\u9886\u53d6",
                        "match_mode": "exact",
                        "timeout_seconds": 15,
                    }
                ],
            )
            actions, error = expand_action_for_run(emitted[0], {})
            self.assertIsNone(error)
            self.assertEqual(actions[0].type, "click")
        finally:
            widget.deleteLater()

    def test_insert_click_uses_selected_resource_id_mode(self) -> None:
        application = QApplication.instance() or QApplication([])
        widget = UiTreeDumpWidget()
        try:
            widget.load_snapshot(self._snapshot())
            widget.tree.setCurrentItem(widget.tree.topLevelItem(0))
            widget.insert_mode_combo.setCurrentIndex(
                widget.insert_mode_combo.findData("resource_id")
            )
            emitted: list[dict[str, object]] = []
            widget.action_inserted.connect(emitted.append)
            widget._insert_click()
            self.assertEqual(
                emitted,
                [
                    {
                        "type": "click",
                        "locate": "ui",
                        "target": "resource_id",
                        "resource_id": "com.example:id/claim",
                        "timeout_seconds": 15,
                    }
                ],
            )
            actions, error = expand_action_for_run(emitted[0], {})
            self.assertIsNone(error)
            self.assertEqual(actions[0].type, "click")
        finally:
            widget.deleteLater()


class TaskValidationLogicTests(unittest.TestCase):
    """Direct tests for the pure logic methods on TaskManagerWidget."""

    @staticmethod
    def _write_task_file(base: Path, data: dict) -> Path:
        tasks_directory = base / "config" / "tasks"
        tasks_directory.mkdir(parents=True, exist_ok=True)
        task_path = tasks_directory / f"{data['id']}.json"
        task_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return task_path

    @staticmethod
    def _make_widget(base: Path) -> TaskManagerWidget:
        application = QApplication.instance() or QApplication([])
        config_directory = base / "config"
        config_directory.mkdir(parents=True, exist_ok=True)
        settings_path = config_directory / "settings.json"
        if not settings_path.exists():
            settings_path.write_text(
                json.dumps({"qq_group_name": "测试群"}, ensure_ascii=False),
                encoding="utf-8",
            )
        return TaskManagerWidget(settings_path, base_directory=base)

    def test_replace_compound_reference_handles_nested_structures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            widget = self._make_widget(Path(directory))
            try:
                value = {
                    "type": "compound",
                    "name": "old",
                    "steps": [
                        {"type": "compound", "name": "keep"},
                        [{"type": "compound", "name": "old"}],
                        {"type": "click", "text": "x"},
                    ],
                }
                changed = TaskManagerWidget._replace_compound_reference(
                    value, "old", "new"
                )
                self.assertTrue(changed)
                self.assertEqual(value["name"], "new")
                self.assertEqual(value["steps"][0]["name"], "keep")
                self.assertEqual(value["steps"][1][0]["name"], "new")
                self.assertEqual(value["steps"][2]["type"], "click")
            finally:
                widget.deleteLater()

    def test_replace_compound_reference_returns_false_when_nothing_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            widget = self._make_widget(Path(directory))
            try:
                value = {"type": "compound", "name": "other", "steps": [{"type": "wait"}]}
                self.assertFalse(
                    TaskManagerWidget._replace_compound_reference(value, "old", "new")
                )
                self.assertEqual(value["name"], "other")
            finally:
                widget.deleteLater()

    def test_compound_reference_updates_collects_only_matching_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            action_directory = base / "config" / "actions"
            action_directory.mkdir(parents=True, exist_ok=True)
            # The referenced compound must exist so the task loads successfully.
            (action_directory / "old.json").write_text(
                json.dumps(
                    {
                        "name": "old",
                        "steps": [{"type": "wait", "seconds": 1}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self._write_task_file(
                base,
                {
                    "id": "has_old",
                    "name": "Has",
                    "package": "demo.package",
                    "actions": [{"type": "compound", "name": "old"}],
                },
            )
            self._write_task_file(
                base,
                {
                    "id": "no_ref",
                    "name": "None",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            widget = self._make_widget(base)
            try:
                updates = widget._compound_reference_updates("old", "new")
                self.assertEqual([item[0] for item in updates], ["has_old"])
                task_id, _original, candidate, task_path = updates[0]
                self.assertEqual(candidate["actions"][0]["name"], "new")
                self.assertEqual(task_path.name, "has_old.json")
            finally:
                widget.deleteLater()

    def test_validate_task_actions_reports_all_error_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            widget = self._make_widget(base)
            try:
                errors = widget._validate_task_actions(
                    [
                        "not-a-dict",
                        {"type": "launch", "wait_seconds": "bad"},
                        {"type": "compound", "name": "missing"},
                        {"type": "wait", "seconds": 1},
                    ]
                )
                self.assertIn("动作 1 必须是对象", errors)
                self.assertTrue(any("动作 2" in error for error in errors))
                self.assertIn("动作 3: 复合动作不存在: missing", errors)
                self.assertEqual(len([e for e in errors if e.startswith("动作 1")]), 1)
            finally:
                widget.deleteLater()

    def test_validate_task_actions_accepts_valid_compound_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            action_directory = base / "config" / "actions"
            action_directory.mkdir(parents=True, exist_ok=True)
            (action_directory / "demo.json").write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "steps": [{"type": "wait", "seconds": 1}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            widget = self._make_widget(base)
            try:
                self.assertEqual(
                    widget._validate_task_actions(
                        [{"type": "compound", "name": "demo"}]
                    ),
                    [],
                )
            finally:
                widget.deleteLater()

    def test_task_path_for_id_finds_file_by_id_attribute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            # File name differs from the id; lookup must scan file contents.
            tasks_directory = base / "config" / "tasks"
            tasks_directory.mkdir(parents=True, exist_ok=True)
            (tasks_directory / "renamed.json").write_text(
                json.dumps(
                    {
                        "id": "demo",
                        "name": "Demo",
                        "package": "demo.package",
                        "actions": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            widget = self._make_widget(base)
            try:
                found = widget._task_path_for_id("demo")
                self.assertIsNotNone(found)
                assert found is not None
                self.assertEqual(found.name, "renamed.json")
                self.assertIsNone(widget._task_path_for_id("missing"))
            finally:
                widget.deleteLater()
