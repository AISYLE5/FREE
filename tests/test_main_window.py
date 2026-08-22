from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from free_app.adb import Device
from free_app.config import load_json
from free_app.models import BatchRunResult, RunResult, RunStatus
from free_app.main_window import MainWindow
from free_app.settings_dialog import SettingsDialog


class FakeScrollBar:
    def __init__(self) -> None:
        self.value = 0

    def maximum(self) -> int:
        return 1

    def setValue(self, value: int) -> None:
        self.value = value


class FakeLogView:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.scroll_bar = FakeScrollBar()

    def appendPlainText(self, line: str) -> None:
        self.lines.append(line)

    def verticalScrollBar(self) -> FakeScrollBar:
        return self.scroll_bar


class LogTarget:
    def __init__(self) -> None:
        self.log_view = FakeLogView()
        self.log_file = io.StringIO()


class MainWindowTests(unittest.TestCase):
    @staticmethod
    def _prepare_config(base: Path) -> None:
        config_directory = base / "config"
        config_directory.mkdir()
        (config_directory / "settings.json").write_text(
            json.dumps({"qq_group_name": "测试群"}, ensure_ascii=False),
            encoding="utf-8",
        )
        project_tasks = Path(__file__).resolve().parents[1] / "config" / "tasks"
        shutil.copytree(project_tasks, config_directory / "tasks")
        project_actions = Path(__file__).resolve().parents[1] / "config" / "actions"
        if project_actions.exists():
            shutil.copytree(project_actions, config_directory / "actions")

    def test_close_event_ignored_when_task_manager_has_unsaved_changes(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            window = MainWindow(base)
            try:
                window._open_task_manager()
                window._task_manager_widget.task_name_edit.setText("changed")
                with patch(
                    "free_app.main_window.MainWindow._confirm_exit_with_unsaved_manager_changes",
                    return_value=False,
                ) as confirm:
                    event = QCloseEvent()
                    window.closeEvent(event)
                confirm.assert_called_once()
                self.assertFalse(event.isAccepted())
            finally:
                window.deleteLater()

    def test_close_event_accepted_without_unsaved_changes(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            window = MainWindow(base)
            try:
                event = QCloseEvent()
                window.closeEvent(event)
                self.assertTrue(event.isAccepted())
            finally:
                window.deleteLater()

    def test_main_window_has_fixed_size_without_maximize_button(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                self.assertEqual(window.size().width(), 1240)
                self.assertEqual(window.size().height(), 820)
                self.assertEqual(window.minimumSize(), window.maximumSize())
                self.assertEqual(window.minimumSize().width(), 1240)
                self.assertEqual(window.minimumSize().height(), 820)
                self.assertFalse(
                    window.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
                )
            finally:
                window.close()
                window.deleteLater()
                application.processEvents()

    def test_main_window_renders_project_config_tasks_at_current_dpi(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                window.show()
                application.processEvents()
                grabbed = window.grab()
                screenshot_path = base / "main-window.png"

                self.assertFalse(grabbed.isNull())
                self.assertGreater(grabbed.width(), 0)
                self.assertGreater(grabbed.height(), 0)
                self.assertTrue(grabbed.save(str(screenshot_path)))
                self.assertGreater(screenshot_path.stat().st_size, 0)
                self.assertEqual(window.task_list.count(), len(window.tasks))
                self.assertGreater(window.task_list.count(), 0)
                self.assertAlmostEqual(
                    grabbed.devicePixelRatio(),
                    window.devicePixelRatioF(),
                )
            finally:
                window.close()
                window.deleteLater()
                application.processEvents()

    def test_device_status_distinguishes_ready_missing_and_query_failure(self) -> None:
        application = QApplication.instance() or QApplication([])

        class FakeAdb:
            def __init__(self, devices: list[Device]) -> None:
                self.devices = devices
                self.connected: list[str] = []

            def connect(self, address: str) -> None:
                self.connected.append(address)

            def list_devices(self) -> list[Device]:
                return self.devices

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                address = "127.0.0.1:16416"
                ready_adb = FakeAdb([Device(address, "device")])
                with patch.object(window, "_make_adb", return_value=ready_adb), patch.object(
                    window, "_mumu_forwarded_adb_address", return_value=address
                ):
                    window._update_device_status()
                self.assertEqual(window.device_label.property("state"), "ready")
                self.assertIn(address, ready_adb.connected)

                missing_adb = FakeAdb([])
                with patch.object(window, "_make_adb", return_value=missing_adb), patch.object(
                    window, "_mumu_forwarded_adb_address", return_value=address
                ):
                    window._update_device_status()
                self.assertEqual(window.device_label.property("state"), "error")

                with patch.object(window, "_make_adb", side_effect=OSError("adb missing")):
                    window._update_device_status()
                self.assertEqual(window.device_label.property("state"), "error")
                self.assertIn("ADB", window.device_label.text())
            finally:
                window.close()
                window.deleteLater()
                application.processEvents()

    def test_prepare_run_creates_worker_and_stop_returns_to_idle(self) -> None:
        application = QApplication.instance() or QApplication([])

        class FakeSignal:
            def connect(self, _callback) -> None:
                return None

        class FakeThread:
            def __init__(self, _parent) -> None:
                self.started = FakeSignal()
                self.finished = FakeSignal()
                self.started_called = False
                self.deleted = False

            def start(self) -> None:
                self.started_called = True

            def quit(self) -> None:
                return None

            def wait(self, _timeout: int) -> bool:
                return True

            def deleteLater(self) -> None:
                self.deleted = True

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            worker = MagicMock()
            worker.log_message = FakeSignal()
            worker.progress = FakeSignal()
            worker.finished = FakeSignal()
            worker.task_started = FakeSignal()
            worker.task_finished = FakeSignal()
            try:
                window.settings["max_log_files"] = 0
                window.run_mode = "single"
                task = window.tasks[0]
                with patch.object(window, "_make_adb", return_value=object()), patch(
                    "free_app.main_window.QThread", FakeThread
                ), patch(
                    "free_app.main_window.TaskWorker", return_value=worker
                ) as worker_class:
                    self.assertTrue(window._prepare_run([task]))

                worker_class.assert_called_once()
                worker.moveToThread.assert_called_once_with(window.worker_thread)
                self.assertTrue(window.worker_thread.started_called)
                self.assertFalse(window.task_list.isEnabled())
                self.assertEqual(window.status_label.property("state"), "running")

                window._stop_task()
                worker.stop.assert_called_once()
                self.assertEqual(window.status_label.property("state"), "stopped")

                with patch.object(window, "_update_device_status"):
                    window._thread_finished()
                self.assertIsNone(window.worker_thread)
                self.assertIsNone(window.worker)
                self.assertTrue(window.task_list.isEnabled())
            finally:
                window.close()
                window.deleteLater()
                application.processEvents()

    def test_prepare_run_debug_passes_debug_flag_to_worker(self) -> None:
        application = QApplication.instance() or QApplication([])

        class FakeSignal:
            def connect(self, _callback) -> None:
                return None

        class FakeThread:
            def __init__(self, _parent) -> None:
                self.started = FakeSignal()
                self.finished = FakeSignal()
                self.started_called = False
                self.deleted = False

            def start(self) -> None:
                self.started_called = True

            def quit(self) -> None:
                return None

            def wait(self, _timeout: int) -> bool:
                return True

            def deleteLater(self) -> None:
                self.deleted = True

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            worker = MagicMock()
            worker.log_message = FakeSignal()
            worker.progress = FakeSignal()
            worker.finished = FakeSignal()
            try:
                window.run_mode = "debug"
                task = window.tasks[0]
                with patch.object(window, "_make_adb", return_value=object()), patch(
                    "free_app.main_window.QThread", FakeThread
                ), patch(
                    "free_app.main_window.TaskWorker", return_value=worker
                ) as worker_class:
                    self.assertTrue(
                        window._prepare_run(
                            [task],
                            log_receiver=lambda _message: None,
                            progress_receiver=lambda _index, _total, _description: None,
                            finished_receiver=lambda _result: None,
                        )
                    )

                self.assertIs(worker_class.call_args.kwargs["debug"], True)
                self.assertIsNone(window.log_file)
                with patch.object(window, "_update_device_status"):
                    window._thread_finished()
            finally:
                window.close()
                window.deleteLater()
                application.processEvents()

    def test_batch_result_signals_update_task_states_and_mark_skipped_tasks(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                first, second = window.tasks[:2]
                window.active_tasks = [first, second]
                window._batch_task_started(first.id, 1, 2)
                window._batch_progress(first.id, 1, 1, "running")
                first_result = RunResult(first.id, RunStatus.SUCCESS, 1, 1)
                window._batch_task_finished(first_result)
                window._batch_finished(
                    BatchRunResult(
                        RunStatus.STOPPED,
                        (first_result,),
                        total_tasks=2,
                        completed_tasks=1,
                    )
                )

                self.assertEqual(window.task_states[first.id], RunStatus.SUCCESS.value)
                self.assertEqual(window.task_states[second.id], "skipped")
                self.assertEqual(window.task_results[first.id], first_result)
                self.assertEqual(window.overall_progress.value(), 1)
            finally:
                window.close()
                window.deleteLater()
                application.processEvents()

    def test_batch_task_finished_increments_task_progress(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                first, second = window.tasks[:2]
                window.active_tasks = [first, second]
                window._batch_task_finished(
                    RunResult(first.id, RunStatus.SUCCESS, 1, 1)
                )
                window._batch_task_finished(
                    RunResult(second.id, RunStatus.SUCCESS, 1, 1)
                )

                self.assertEqual(window.overall_progress.value(), 2)
                self.assertEqual(window.overall_count_label.text(), "全部任务 2 / 2")
            finally:
                window.close()
                window.deleteLater()
                application.processEvents()

    def test_task_order_is_persisted_after_list_reordering(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                first_id = str(window.task_list.item(0).data(Qt.ItemDataRole.UserRole))
                moved = window.task_list.takeItem(0)
                window.task_list.addItem(moved)

                window._save_task_order()

                saved = load_json(window.settings_path)
                self.assertEqual(saved["task_order"][-1], first_id)
                self.assertEqual(
                    saved["task_order"],
                    [
                        str(window.task_list.item(i).data(Qt.ItemDataRole.UserRole))
                        for i in range(window.task_list.count())
                    ],
                )
            finally:
                window.close()
                window.deleteLater()
                application.processEvents()

    def test_refresh_device_button_has_busy_guard_and_restores_state(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                with patch.object(window, "_update_device_status") as update, patch.object(
                    window, "_finalize_refresh_button"
                ) as finalize:
                    update.side_effect = lambda finalize_refresh=False: finalize()
                    window._refresh_device()
                    window._refresh_device()

                update.assert_called_once_with(finalize_refresh=True)
                finalize.assert_called_once()
                self.assertFalse(window.refresh_button.isEnabled())
                self.assertIn("刷新中", window.refresh_button.text())

                window._restore_refresh_button()
                self.assertTrue(window.refresh_button.isEnabled())
                self.assertEqual(window.refresh_button.text(), "刷新")
            finally:
                window.close()
                window.deleteLater()
                application.processEvents()

    def test_refresh_tasks_reloads_list_and_reports_broken_files(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                original_count = window.task_list.count()
                (base / "config" / "tasks" / "broken.json").write_text("{", encoding="utf-8")
                with patch("free_app.main_window.QMessageBox.warning") as warning, patch.object(
                    window, "_refresh_device"
                ) as device_refresh:
                    window._refresh_all()

                self.assertEqual(window.task_list.count(), original_count)
                self.assertEqual(len(window.config_errors), 1)
                self.assertEqual(window.config_errors[0].path.name, "broken.json")
                warning.assert_called_once()
                self.assertIn("broken.json已损坏，跳过该任务", warning.call_args.args[2])
                device_refresh.assert_called_once()
            finally:
                window.close()
                window.deleteLater()
                application.processEvents()

    def test_settings_page_can_open_and_return_to_main_page(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"), patch.object(
                SettingsDialog, "_refresh_mumu_instances"
            ):
                window = MainWindow(base)
                try:
                    window._open_settings()
                    application.processEvents()
                    self.assertIs(window.pages.currentWidget(), window.settings_page)
                    self.assertIsNotNone(window._settings_widget)

                    window._on_settings_back()
                    self.assertIs(window.pages.currentWidget(), window.main_page)
                finally:
                    window.close()
                    window.deleteLater()
                    application.processEvents()

    def test_task_manager_button_sits_left_of_run_all(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                window.show()
                application.processEvents()
                self.assertLess(
                    window.task_manager_button.geometry().center().x(),
                    window.run_all_button.geometry().center().x(),
                )
                self.assertTrue(window.task_manager_button.isVisible())
            finally:
                window.close()
                window.deleteLater()
                application.processEvents()

    def test_task_manager_page_can_open_and_return_to_main_page(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                window._open_task_manager()
                application.processEvents()
                self.assertIs(window.pages.currentWidget(), window.task_manager_page)
                self.assertIsNotNone(window._task_manager_widget)
                self.assertEqual(window._task_manager_widget.task_list.count(), len(window.tasks))

                window._close_task_manager_page()
                self.assertIs(window.pages.currentWidget(), window.main_page)
            finally:
                window.close()
                window.deleteLater()
                application.processEvents()

    def test_task_manager_back_returns_through_embedded_page_before_closing(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                window._open_task_manager()
                manager = window._task_manager_widget
                manager.actions_list.setCurrentRow(0)
                manager._edit_action()
                application.processEvents()

                window._task_manager_back()
                self.assertIs(window.pages.currentWidget(), window.task_manager_page)
                self.assertIs(
                    manager.editor_stack.currentWidget(),
                    manager.editor_stack.widget(manager.left_tabs.currentIndex()),
                )

                window._task_manager_back()
                self.assertIs(window.pages.currentWidget(), window.main_page)
            finally:
                window.close()
                window.deleteLater()
                application.processEvents()

    def test_task_manager_copy_package_button_sits_left_of_dump_tree(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                window.show()
                application.processEvents()
                window._open_task_manager()
                application.processEvents()
                self.assertTrue(window.task_manager_copy_package_button.isVisible())
                self.assertEqual(window.task_manager_copy_package_button.text(), "获取包名")
                with patch("free_app.main_window.QToolTip.showText") as tooltip:
                    window._show_task_manager_feedback("tv.danmaku.bili")
                self.assertEqual(tooltip.call_args.args[1], "tv.danmaku.bili")
                self.assertLess(
                    window.task_manager_copy_package_button.geometry().center().x(),
                    window.task_manager_dump_tree_button.geometry().center().x(),
                )
            finally:
                window.close()
                window.deleteLater()
                application.processEvents()

    def test_task_manager_run_action_button_starts_single_task(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                window._open_task_manager()
                manager = window._task_manager_widget
                manager.task_name_edit.setText("测试动作")
                manager.task_package_edit.setText("demo.package")
                manager._actions_buffer = [{"type": "wait", "seconds": 1}]
                manager._refresh_actions_list()
                manager.actions_list.setCurrentRow(0)

                with patch.object(window, "_prepare_run", return_value=True) as prepare:
                    manager.run_action_button.click()

                prepare.assert_called_once()
                task = prepare.call_args.args[0][0]
                self.assertEqual(task.id, "_single_action_test")
                self.assertEqual(task.name, "测试动作")
                self.assertEqual(task.package, "demo.package")
                self.assertEqual([action.type for action in task.actions], ["wait"])
                self.assertEqual(window.run_mode, "debug")
                self.assertIs(window.pages.currentWidget(), window.task_manager_page)
                self.assertIs(
                    manager.editor_stack.currentWidget(),
                    manager.embedded_run_viewer_panel,
                )
            finally:
                with patch(
                    "free_app.main_window.MainWindow._confirm_exit_with_unsaved_manager_changes",
                    return_value=True,
                ):
                    window.close()
                window.deleteLater()
                application.processEvents()

    def test_task_manager_run_compound_action_expands_before_starting(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                window._open_task_manager()
                manager = window._task_manager_widget
                manager.task_name_edit.setText("处理广告")
                manager.task_package_edit.setText("tv.danmaku.bili")
                manager._actions_buffer = [
                    {"type": "compound", "name": "bilibili_ad"}
                ]
                manager._refresh_actions_list()
                manager.actions_list.setCurrentRow(0)

                with patch.object(window, "_prepare_run", return_value=True) as prepare:
                    manager.run_action_button.click()

                task = prepare.call_args.args[0][0]
                self.assertEqual(
                    [action.type for action in task.actions],
                    ["wait", "click", "wait", "back", "wait"],
                )
            finally:
                with patch(
                    "free_app.main_window.MainWindow._confirm_exit_with_unsaved_manager_changes",
                    return_value=True,
                ):
                    window.close()
                window.deleteLater()
                application.processEvents()

    def test_task_manager_run_action_substitutes_variables(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                window._open_task_manager()
                manager = window._task_manager_widget
                manager.task_package_edit.setText("tv.danmaku.bili")
                manager._actions_buffer = [
                    {
                        "type": "click",
                        "locate": "ui",
                        "target": "text",
                        "text": "${qq_group_name}",
                    }
                ]
                manager._refresh_actions_list()
                manager.actions_list.setCurrentRow(0)

                with patch.object(window, "_prepare_run", return_value=True) as prepare:
                    manager.run_action_button.click()

                task = prepare.call_args.args[0][0]
                self.assertEqual(task.actions[0].parameters["text"], "测试群")
            finally:
                with patch(
                    "free_app.main_window.MainWindow._confirm_exit_with_unsaved_manager_changes",
                    return_value=True,
                ):
                    window.close()
                window.deleteLater()
                application.processEvents()

    def test_task_manager_run_step_uses_task_package_and_launch_wait(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"):
                window = MainWindow(base)
            try:
                window._open_task_manager()
                manager = window._task_manager_widget
                manager.task_package_edit.setText("tv.danmaku.bili")
                manager._steps_buffer = [{"type": "launch", "wait_seconds": 7}]
                manager._refresh_steps_list()
                manager.compound_steps_list.setCurrentRow(0)

                with patch.object(window, "_prepare_run", return_value=True) as prepare:
                    manager._run_single_step()

                task = prepare.call_args.args[0][0]
                self.assertEqual(task.package, "tv.danmaku.bili")
                self.assertEqual([action.type for action in task.actions], ["launch"])
                self.assertEqual(task.actions[0].parameters["wait_seconds"], 7)
            finally:
                with patch(
                    "free_app.main_window.MainWindow._confirm_exit_with_unsaved_manager_changes",
                    return_value=True,
                ):
                    window.close()
                window.deleteLater()
                application.processEvents()

    def test_settings_ocr_log_is_written_to_home_log(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self._prepare_config(base)
            with patch.object(MainWindow, "_update_device_status"), patch.object(
                SettingsDialog, "_refresh_mumu_instances"
            ):
                window = MainWindow(base)
                try:
                    window._open_settings()
                    application.processEvents()
                    window._settings_widget.log_message.emit(
                        "OCR 测试结果：识别成功，共 2 行"
                    )
                    window._settings_widget.ocr_test_finished.emit()
                    application.processEvents()

                    self.assertIn(
                        "OCR 测试结果：识别成功，共 2 行",
                        window.log_view.toPlainText(),
                    )
                    self.assertIs(window.pages.currentWidget(), window.main_page)
                finally:
                    window.close()
                    window.deleteLater()
                    application.processEvents()

    def test_append_log_preserves_worker_timestamp(self) -> None:
        target = LogTarget()

        MainWindow._append_log(target, "[12:34:56] worker message")

        self.assertEqual(target.log_view.lines, ["[12:34:56] worker message"])
        self.assertEqual(target.log_file.getvalue(), "[12:34:56] worker message\n")

    def test_append_log_adds_timestamp_to_plain_message(self) -> None:
        target = LogTarget()

        MainWindow._append_log(target, "plain message")

        self.assertRegex(target.log_view.lines[0], r"^\[\d{2}:\d{2}:\d{2}\] plain message$")

    def test_append_log_writes_every_line(self) -> None:
        target = LogTarget()

        MainWindow._append_log(target, "[12:34:56] OCR 点击候选: ['领取']")
        MainWindow._append_log(target, "[12:34:56] ADB tap: (540, 960)")
        MainWindow._append_log(target, "[12:34:56] 任务结果 [1/1] demo: success")

        self.assertEqual(
            target.log_view.lines,
            [
                "[12:34:56] OCR 点击候选: ['领取']",
                "[12:34:56] ADB tap: (540, 960)",
                "[12:34:56] 任务结果 [1/1] demo: success",
            ],
        )
        self.assertEqual(
            target.log_file.getvalue(),
            "[12:34:56] OCR 点击候选: ['领取']\n"
            "[12:34:56] ADB tap: (540, 960)\n"
            "[12:34:56] 任务结果 [1/1] demo: success\n",
        )

    def test_subtitle_reads_instance_index_and_fixed_screen_constants(self) -> None:
        class FakeLabel:
            def __init__(self) -> None:
                self.text = ""

            def setText(self, text: str) -> None:
                self.text = text

        class SubtitleTarget:
            settings = {"mumu_vm_index": 3}

            def __init__(self) -> None:
                self.subtitle_label = FakeLabel()

        target = SubtitleTarget()
        MainWindow._update_subtitle(target)
        self.assertEqual(target.subtitle_label.text, "实例 3  ·  1080×1920  ·  480 dpi")

        target.settings = {}
        MainWindow._update_subtitle(target)
        self.assertEqual(target.subtitle_label.text, "实例 0  ·  1080×1920  ·  480 dpi")

    def test_effective_screenshots_enabled_follows_max_files(self) -> None:
        class Target:
            settings: dict = {}

        Target.settings = {"max_screenshot_files": 0}
        self.assertIs(MainWindow._effective_screenshots_enabled(Target()), False)
        Target.settings = {"max_screenshot_files": -1}
        self.assertIs(MainWindow._effective_screenshots_enabled(Target()), True)
        Target.settings = {"max_screenshot_files": 5}
        self.assertIs(MainWindow._effective_screenshots_enabled(Target()), True)
        Target.settings = {"screenshot_save_level": "all"}
        self.assertIs(MainWindow._effective_screenshots_enabled(Target()), True)
        Target.settings = {}
        self.assertIs(MainWindow._effective_screenshots_enabled(Target()), True)


if __name__ == "__main__":
    unittest.main()
