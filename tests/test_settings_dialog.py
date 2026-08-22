from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QDialog

from free_app.ocr_models import DownloadCancelled
from free_app.settings_dialog import ModelDownloadWorker, SettingsComboBox, SettingsDialog
from free_app.settings_dialog import _build_confirm_message_box


class SettingsDialogTests(unittest.TestCase):
    def _make_dialog(self, base: Path) -> SettingsDialog:
        application = QApplication.instance() or QApplication([])
        config_directory = base / "config"
        config_directory.mkdir(exist_ok=True)
        settings_path = config_directory / "settings.json"
        if not settings_path.exists():
            settings_path.write_text("{}", encoding="utf-8")
        tasks_directory = config_directory / "tasks"
        if not tasks_directory.exists():
            project_tasks = Path(__file__).resolve().parents[1] / "config" / "tasks"
            shutil.copytree(project_tasks, tasks_directory)
        actions_directory = config_directory / "actions"
        if not actions_directory.exists():
            project_actions = Path(__file__).resolve().parents[1] / "config" / "actions"
            shutil.copytree(project_actions, actions_directory)
        with patch.object(SettingsDialog, "_refresh_mumu_instances"):
            return SettingsDialog(
                settings_path,
                base_directory=base,
            )

    def test_settings_combo_does_not_switch_with_mouse_wheel(self) -> None:
        QApplication.instance() or QApplication([])
        combo = SettingsComboBox()
        combo.addItem("第一项", "first")
        combo.addItem("第二项", "second")
        combo.setCurrentIndex(0)
        event = QWheelEvent(
            QPointF(4, 4),
            QPointF(4, 4),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )

        combo.wheelEvent(event)

        self.assertEqual(combo.currentIndex(), 0)
        self.assertFalse(event.isAccepted())

    def test_model_download_worker_emits_terminal_signal_for_each_outcome(self) -> None:
        outcomes = (
            (None, "succeeded"),
            (DownloadCancelled("cancelled"), "cancelled"),
            (RuntimeError("network unavailable"), "failed"),
        )
        for error, signal_name in outcomes:
            with self.subTest(signal_name=signal_name):
                worker = ModelDownloadWorker("PP-OCRv6_small_det", Path("models"))
                received: list[object] = []
                getattr(worker, signal_name).connect(lambda *args: received.append(args))
                with patch("free_app.settings_dialog.download_model", side_effect=error):
                    worker.run()

                expected = (
                    ("PP-OCRv6_small_det",)
                    if signal_name != "failed"
                    else ("PP-OCRv6_small_det", "network unavailable")
                )
                self.assertEqual(received, [expected])

    def test_download_progress_and_finished_cleanup_update_model_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dialog = self._make_dialog(base)
            thread = MagicMock()
            dialog._download_threads["PP-OCRv6_small_det"] = thread
            dialog._download_workers["PP-OCRv6_small_det"] = MagicMock()
            try:
                dialog._on_download_progress("PP-OCRv6_small_det", 50, 100)
                self.assertIn("50", dialog._model_action["PP-OCRv6_small_det"].text())
                dialog._on_download_progress("PP-OCRv6_small_det", 1, 0)
                self.assertTrue(dialog._model_action["PP-OCRv6_small_det"].text())

                dialog._download_finished("PP-OCRv6_small_det")
                self.assertNotIn("PP-OCRv6_small_det", dialog._download_threads)
                self.assertNotIn("PP-OCRv6_small_det", dialog._download_workers)
                thread.deleteLater.assert_called_once()
            finally:
                dialog.deleteLater()

    def test_test_ocr_reports_missing_selected_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dialog = self._make_dialog(base)
            try:
                with patch("free_app.settings_dialog.QMessageBox.warning") as warning:
                    dialog._test_ocr()

                warning.assert_called_once()
            finally:
                dialog.deleteLater()

    def test_test_ocr_reports_local_image_read_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dialog = self._make_dialog(base)
            try:
                with patch("free_app.settings_dialog.OnnxOcrClient.models_ready", return_value=True), patch(
                    "free_app.settings_dialog.QFileDialog.getOpenFileName",
                    return_value=(str(base / "missing.png"), "PNG"),
                ), patch.object(Path, "read_bytes", side_effect=OSError("image unavailable")), patch(
                    "free_app.settings_dialog.QMessageBox.warning"
                ) as warning:
                    image, source = dialog._capture_test_image()

                self.assertIsNone(image)
                self.assertEqual(source, "")
                warning.assert_called_once()
            finally:
                dialog.deleteLater()

    def test_test_ocr_emits_result_to_log_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dialog = self._make_dialog(base)
            try:
                logs: list[str] = []
                finished: list[bool] = []
                dialog.log_message.connect(logs.append)
                dialog.ocr_test_finished.connect(lambda: finished.append(True))
                with patch(
                    "free_app.settings_dialog.OnnxOcrClient.models_ready",
                    return_value=True,
                ), patch.object(
                    dialog,
                    "_capture_test_image",
                    return_value=(b"image-data", "测试图片"),
                ), patch(
                    "free_app.settings_dialog.OnnxOcrClient.recognize",
                    return_value=["你好", "世界"],
                ):
                    dialog._test_ocr()

                self.assertTrue(any("OCR 测试结果：识别成功" in line for line in logs))
                self.assertTrue(any("OCR 识别: 你好" in line for line in logs))
                self.assertTrue(any("OCR 识别: 世界" in line for line in logs))
                self.assertEqual(finished, [True])
                self.assertTrue(dialog.test_ocr_button.isEnabled())
                self.assertEqual(dialog.test_ocr_button.text(), "测试识别")
            finally:
                dialog.deleteLater()

    def test_test_ocr_emits_failure_to_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dialog = self._make_dialog(base)
            try:
                logs: list[str] = []
                finished: list[bool] = []
                dialog.log_message.connect(logs.append)
                dialog.ocr_test_finished.connect(lambda: finished.append(True))
                with patch(
                    "free_app.settings_dialog.OnnxOcrClient.models_ready",
                    return_value=True,
                ), patch.object(
                    dialog,
                    "_capture_test_image",
                    return_value=(b"image-data", "测试图片"),
                ), patch(
                    "free_app.settings_dialog.OnnxOcrClient.recognize",
                    side_effect=RuntimeError("模型推理失败"),
                ):
                    dialog._test_ocr()

                self.assertTrue(any("OCR 测试失败" in line for line in logs))
                self.assertTrue(any("模型推理失败" in line for line in logs))
                self.assertEqual(finished, [True])
            finally:
                dialog.deleteLater()

    def test_refresh_mumu_instances_uses_cli_names_and_preserves_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dialog = self._make_dialog(base)
            try:
                dialog.settings["mumu_vm_index"] = 2
                controller = MagicMock()
                controller.list_instances.return_value = {0: "0", 2: "Work"}
                with patch("free_app.settings_dialog.MuMuController", return_value=controller):
                    dialog._refresh_mumu_instances()

                self.assertEqual(
                    [dialog.mumu_vm_index_combo.itemText(i) for i in range(dialog.mumu_vm_index_combo.count())],
                    ["#0", "#2 Work"],
                )
                self.assertEqual(dialog.mumu_vm_index_combo.currentData(), 2)
            finally:
                dialog.deleteLater()

    def test_refresh_mumu_instances_falls_back_to_default_indices_on_cli_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dialog = self._make_dialog(base)
            try:
                with patch(
                    "free_app.settings_dialog.MuMuController",
                    side_effect=ValueError("unexpected"),
                ):
                    dialog._refresh_mumu_instances()

                self.assertEqual(dialog.mumu_vm_index_combo.count(), 10)
                self.assertEqual(dialog.mumu_vm_index_combo.itemData(0), 0)
                self.assertEqual(dialog.mumu_vm_index_combo.itemData(9), 9)
            finally:
                dialog.deleteLater()

    def test_send_test_email_restores_button_for_success_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dialog = self._make_dialog(base)
            try:
                with patch("free_app.settings_dialog.send_run_notification", return_value=True) as send, patch(
                    "free_app.settings_dialog.QMessageBox.information"
                ) as information:
                    dialog._send_test_email()
                send.assert_called_once()
                information.assert_called_once()
                self.assertTrue(dialog.test_button.isEnabled())

                with patch("free_app.settings_dialog.send_run_notification", return_value=False), patch(
                    "free_app.settings_dialog.QMessageBox.warning"
                ) as warning:
                    dialog._send_test_email()
                warning.assert_called_once()
                self.assertTrue(dialog.test_button.isEnabled())
            finally:
                dialog.deleteLater()

    def test_save_discovers_mumu_program_paths_from_selected_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            mumu = base / "mumu"
            (mumu / "nx_main").mkdir(parents=True)
            adb_path = mumu / "nx_main" / "adb.exe"
            cli_path = mumu / "nx_main" / "mumu-cli.exe"
            adb_path.write_bytes(b"")
            cli_path.write_bytes(b"")
            settings_path = base / "config" / "settings.json"
            dialog = self._make_dialog(base)
            try:
                dialog.mumu_directory_edit.setText(str(mumu))
                dialog._save()
                saved = json.loads(settings_path.read_text(encoding="utf-8"))

                self.assertEqual(saved["adb_path"], str(adb_path))
                self.assertEqual(saved["mumu_cli_path"], str(cli_path))
            finally:
                dialog.deleteLater()

    def test_dialog_renders_at_current_dpi(self) -> None:
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dialog = self._make_dialog(base)
            try:
                dialog.show()
                application.processEvents()
                grabbed = dialog.grab()
                screenshot_path = base / "settings-dialog.png"

                self.assertFalse(grabbed.isNull())
                self.assertGreater(grabbed.width(), 0)
                self.assertGreater(grabbed.height(), 0)
                self.assertTrue(grabbed.save(str(screenshot_path)))
                self.assertGreater(screenshot_path.stat().st_size, 0)
                self.assertAlmostEqual(
                    grabbed.devicePixelRatio(),
                    dialog.devicePixelRatioF(),
                )
            finally:
                dialog.close()
                dialog.deleteLater()
                application.processEvents()

    def test_has_unsaved_changes_is_false_when_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps(
                    {
                        "max_log_files": 5,
                        "max_screenshot_files": 5,
                        "cleanup_mode": "recycle",
                        "stale_field": 99,
                        "stale_counts": {"hanserclub": 9},
                        "stale_log": "summary",
                        "stale_enabled": True,
                        "stale_screenshot": True,
                        "stale_device": "emulator-5556",
                        "email_notification": {"stale_email_field": "stale@example.com"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            try:
                self.assertFalse(dialog._has_unsaved_changes())
            finally:
                dialog.deleteLater()

    def test_has_unsaved_changes_is_true_after_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            try:
                dialog.qq_group_name_edit.setText("另一个群")
                self.assertTrue(dialog._has_unsaved_changes())
            finally:
                dialog.deleteLater()

    def test_has_unsaved_changes_detects_mumu_app_switch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            try:
                dialog.close_mumu_app_after_run.setCurrentIndex(0)
                self.assertTrue(dialog._has_unsaved_changes())
            finally:
                dialog.deleteLater()

    def test_clear_output_files_confirms_and_cleans_with_current_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            log_directory = base / "logs"
            log_directory.mkdir()
            log_file = log_directory / "old.log"
            log_file.write_text("old", encoding="utf-8")
            dialog = self._make_dialog(base)
            try:
                dialog.cleanup_mode_combo.setCurrentIndex(1)
                with patch("free_app.settings_dialog.confirm_dialog", return_value=True), patch(
                    "free_app.settings_dialog.QMessageBox.information"
                ) as information:
                    dialog._clear_output_files("logs")

                self.assertFalse(log_file.exists())
                information.assert_called_once()
            finally:
                dialog.deleteLater()

    def test_reject_without_changes_does_not_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            with patch("free_app.settings_dialog.QMessageBox.question") as question:
                dialog.reject()
            question.assert_not_called()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
            dialog.deleteLater()

    def test_reject_with_changes_confirms_discard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            dialog.smtp_host.setText("smtp.example.com")
            with patch.object(SettingsDialog, "_confirm_discard", return_value=True) as confirm:
                dialog.reject()
            confirm.assert_called_once()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
            dialog.deleteLater()

    def test_reject_with_changes_aborts_when_user_keeps_editing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            dialog.max_log_files_edit.setText("88")
            with patch.object(SettingsDialog, "_confirm_discard", return_value=False) as confirm:
                dialog.reject()
            confirm.assert_called_once()
            self.assertEqual(dialog.result(), 0)
            dialog.deleteLater()

    def test_confirm_message_box_buttons_cancel_left_confirm_right(self) -> None:
        message_box = _build_confirm_message_box(None, "标题", "内容")
        self.assertEqual(
            [button.text() for button in message_box.buttons()],
            ["取消", "确认"],
        )

    def test_delete_model_uses_confirm_dialog_and_aborts_on_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            with patch("free_app.settings_dialog.confirm_dialog", return_value=False) as confirm, patch(
                "free_app.settings_dialog.delete_model"
            ) as delete:
                dialog._delete_model("PP-OCRv6_small_rec")
            confirm.assert_called_once()
            delete.assert_not_called()
            dialog.deleteLater()

    def test_delete_selected_model_clears_selection_when_none_left(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            model_dir = base / "models" / "PP-OCRv6_small_det"
            model_dir.mkdir(parents=True)
            (model_dir / "inference.onnx").write_bytes(b"x")
            dialog = self._make_dialog(base)
            radio = dialog._model_radios["PP-OCRv6_small_det"]
            self.assertTrue(radio.isChecked())

            def fake_delete(name: str, root: Path, mode: str = "recycle") -> Path:
                shutil.rmtree(root / name)
                return root / name

            with patch("free_app.settings_dialog.confirm_dialog", return_value=True), patch(
                "free_app.settings_dialog.delete_model", side_effect=fake_delete
            ), patch("free_app.settings_dialog.QMessageBox.information"):
                dialog._delete_model("PP-OCRv6_small_det")

            self.assertFalse(radio.isChecked())
            self.assertFalse(radio.isEnabled())
            dialog.deleteLater()

    def test_delete_selected_model_moves_selection_to_next_installed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            for name in ("PP-OCRv6_small_det", "PP-OCRv6_tiny_det"):
                model_dir = base / "models" / name
                model_dir.mkdir(parents=True)
                (model_dir / "inference.onnx").write_bytes(b"x")
            dialog = self._make_dialog(base)
            small = dialog._model_radios["PP-OCRv6_small_det"]
            tiny = dialog._model_radios["PP-OCRv6_tiny_det"]
            self.assertTrue(small.isChecked())

            def fake_delete(name: str, root: Path, mode: str = "recycle") -> Path:
                shutil.rmtree(root / name)
                return root / name

            with patch("free_app.settings_dialog.confirm_dialog", return_value=True), patch(
                "free_app.settings_dialog.delete_model", side_effect=fake_delete
            ), patch("free_app.settings_dialog.QMessageBox.information"):
                dialog._delete_model("PP-OCRv6_small_det")

            self.assertTrue(tiny.isChecked())
            self.assertFalse(small.isChecked())
            dialog.deleteLater()

    def test_refresh_unchecks_radios_of_missing_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            model_dir = base / "models" / "PP-OCRv6_small_det"
            model_dir.mkdir(parents=True)
            (model_dir / "inference.onnx").write_bytes(b"x")
            dialog = self._make_dialog(base)
            radio = dialog._model_radios["PP-OCRv6_small_det"]
            self.assertTrue(radio.isChecked())
            shutil.rmtree(model_dir)

            dialog._refresh_model_statuses()

            self.assertFalse(radio.isChecked())
            self.assertFalse(radio.isEnabled())
            dialog.deleteLater()

    def test_clicking_model_row_card_selects_radio(self) -> None:
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent, QPointingDevice

        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(30, 20),
            QPointF(30, 20),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            QPointingDevice.primaryPointingDevice(),
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            for name in ("PP-OCRv6_small_det", "PP-OCRv6_tiny_det"):
                model_dir = base / "models" / name
                model_dir.mkdir(parents=True)
                (model_dir / "inference.onnx").write_bytes(b"x")
            dialog = self._make_dialog(base)
            small = dialog._model_radios["PP-OCRv6_small_det"]
            tiny = dialog._model_radios["PP-OCRv6_tiny_det"]
            self.assertTrue(small.isChecked())

            dialog._model_cards["PP-OCRv6_tiny_det"].mouseReleaseEvent(release)
            self.assertTrue(tiny.isChecked())
            self.assertFalse(small.isChecked())

            dialog._model_cards["PP-OCRv6_medium_det"].mouseReleaseEvent(release)
            self.assertTrue(tiny.isChecked())
            dialog.deleteLater()

    def test_model_radio_dot_is_clickable(self) -> None:
        from PySide6.QtCore import Qt

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            model_dir = base / "models" / "PP-OCRv6_small_det"
            model_dir.mkdir(parents=True)
            (model_dir / "inference.onnx").write_bytes(b"x")
            dialog = self._make_dialog(base)
            radio = dialog._model_radios["PP-OCRv6_small_det"]
            self.assertFalse(radio.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
            dialog.deleteLater()

    def test_toggle_model_action_cancels_when_downloading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            dialog._download_threads["PP-OCRv6_tiny_det"] = object()
            with patch.object(dialog, "_cancel_download") as cancel, patch.object(
                dialog, "_start_download"
            ) as start:
                dialog._toggle_model_action("PP-OCRv6_tiny_det")
            cancel.assert_called_once_with("PP-OCRv6_tiny_det")
            start.assert_not_called()
            dialog.deleteLater()

    def test_cancel_download_requests_worker_cancel(self) -> None:
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            worker = MagicMock()
            dialog._download_workers["PP-OCRv6_tiny_det"] = worker
            dialog._download_threads["PP-OCRv6_tiny_det"] = object()

            dialog._cancel_download("PP-OCRv6_tiny_det")

            worker.cancel.assert_called_once()
            self.assertEqual(dialog._model_action["PP-OCRv6_tiny_det"].text(), "取消中…")
            dialog.deleteLater()

    def test_refresh_model_status_detects_newly_installed_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            model_dir = base / "models" / "PP-OCRv6_small_det"
            model_dir.mkdir(parents=True)
            (model_dir / "inference.onnx").write_bytes(b"x")
            radio = dialog._model_radios["PP-OCRv6_small_det"]
            self.assertFalse(radio.isEnabled())

            dialog._refresh_model_status()

            self.assertTrue(radio.isEnabled())
            self.assertEqual(dialog._model_action["PP-OCRv6_small_det"].text(), "删除")
            self.assertIn("1 个模型已下载", dialog.ocr_feedback_label.text())
            dialog.deleteLater()

    def test_security_switch_fills_default_port_when_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            dialog.smtp_port.setText("")
            dialog.smtp_security.setCurrentIndex(1)
            self.assertEqual(dialog.smtp_port.text(), "587")
            dialog.smtp_security.setCurrentIndex(0)
            self.assertEqual(dialog.smtp_port.text(), "465")
            dialog.deleteLater()

    def test_security_options_exclude_plain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            self.assertEqual(
                [dialog.smtp_security.itemData(i) for i in range(dialog.smtp_security.count())],
                ["ssl", "starttls"],
            )
            dialog.smtp_port.setText("")
            dialog.smtp_security.setCurrentIndex(1)
            self.assertEqual(dialog.smtp_port.text(), "587")
            dialog.deleteLater()

    def test_security_switch_keeps_custom_port(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            dialog.smtp_port.setText("2525")
            dialog.smtp_security.setCurrentIndex(1)
            self.assertEqual(dialog.smtp_port.text(), "2525")
            dialog.deleteLater()

    def test_custom_port_is_preserved_when_loading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps(
                    {
                        "email_notification": {
                            "smtp_port": 2525,
                            "smtp_security": "starttls",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            self.assertEqual(dialog.smtp_port.text(), "2525")
            self.assertEqual(dialog.smtp_security.currentData(), "starttls")
            dialog.deleteLater()

    def test_collect_settings_omits_sender(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps({}, ensure_ascii=False),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            email = dialog._collect_settings()["email_notification"]
            self.assertNotIn("sender", email)
            dialog.deleteLater()

    def test_collect_settings_includes_retention_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            (base / "config" / "settings.json").write_text(
                json.dumps(
                    {
                        "max_log_files": 5,
                        "max_screenshot_files": 5,
                        "cleanup_mode": "recycle",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            collected = dialog._collect_settings()
            self.assertEqual(collected["max_log_files"], 5)
            self.assertEqual(collected["max_screenshot_files"], 5)
            self.assertEqual(collected["cleanup_mode"], "recycle")
            self.assertEqual(collected["mumu_vm_index"], 0)
            self.assertTrue(collected["task_execution_counts"])
            self.assertEqual(collected["task_execution_counts"]["hanserclub"], 1)
            dialog.deleteLater()

    def test_screenshot_save_level_is_no_longer_editable_or_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            settings_path = base / "config" / "settings.json"
            settings_path.write_text(
                json.dumps({"screenshot_save_level": "all"}, ensure_ascii=False),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            try:
                self.assertFalse(hasattr(dialog, "screenshot_save_level_combo"))
                self.assertFalse(dialog._has_unsaved_changes())
                collected = dialog._collect_settings()
                self.assertNotIn("screenshot_save_level", collected)
                dialog._save()
                saved = json.loads(settings_path.read_text(encoding="utf-8"))
                self.assertNotIn("screenshot_save_level", saved)
            finally:
                dialog.deleteLater()

    def test_screenshot_limit_field_is_not_affected_by_stale_level(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            settings_path = base / "config" / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {"screenshot_save_level": "all", "max_screenshot_files": 5},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            try:
                self.assertTrue(dialog.max_screenshot_files_edit.isEnabled())
                self.assertEqual(dialog.max_screenshot_files_edit.text(), "5")
                self.assertFalse(dialog._has_unsaved_changes())
                dialog.max_screenshot_files_edit.setText("999")
                self.assertTrue(dialog._has_unsaved_changes())
            finally:
                dialog.deleteLater()

    def test_download_source_combo_loads_saves_and_detects_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            settings_path = base / "config" / "settings.json"
            settings_path.write_text(
                json.dumps({"ocr_download_source": "modelscope"}, ensure_ascii=False),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            try:
                self.assertEqual(dialog.download_source_combo.currentData(), "modelscope")
                self.assertFalse(dialog._has_unsaved_changes())
                dialog.download_source_combo.setCurrentIndex(0)
                self.assertTrue(dialog._has_unsaved_changes())
                dialog._save()
                saved = json.loads(settings_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["ocr_download_source"], "auto")
            finally:
                dialog.deleteLater()

    def test_model_download_worker_passes_selected_source(self) -> None:
        worker = ModelDownloadWorker("PP-OCRv6_small_det", Path("models"), "huggingface")
        with patch("free_app.settings_dialog.download_model") as download:
            worker.run()
        download.assert_called_once()
        self.assertEqual(download.call_args.kwargs["source"], "huggingface")

    def test_save_writes_smtp_credentials_into_single_settings_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            settings_path = base / "config" / "settings.json"
            settings_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
            dialog = self._make_dialog(base)
            dialog.mumu_directory_edit.setText("")
            dialog._task_execution_combos["hanserclub"].setValue(4)
            dialog.smtp_username.setText("new@qq.com")
            dialog._save()
            saved = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["email_notification"]["smtp_username"], "new@qq.com")
            self.assertEqual(saved["task_execution_counts"]["hanserclub"], 4)
            self.assertNotIn("screenshot_save_level", saved)
            self.assertIn("smtp_password", saved["email_notification"])
            self.assertEqual(saved["mumu_directory"], "")
            self.assertEqual(saved["qq_group_name"], "")
            self.assertFalse((base / "config" / "settings.local.json").exists())
            dialog.deleteLater()

    def test_save_writes_only_native_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            settings_path = base / "config" / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "max_log_files": 5,
                        "max_screenshot_files": 5,
                        "cleanup_mode": "recycle",
                        "stale_field": "x",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            dialog = self._make_dialog(base)
            dialog._save()
            saved = json.loads(settings_path.read_text(encoding="utf-8"))

            self.assertNotIn("stale_field", saved)
            self.assertNotIn("stale_email_field", saved["email_notification"])
            self.assertEqual(saved["max_log_files"], 5)
            self.assertEqual(saved["task_execution_counts"]["hanserclub"], 1)
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
