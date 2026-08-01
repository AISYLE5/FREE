from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from free_app.config import TaskFileError
from free_app.models import BatchRunResult, RunResult, RunStatus, TaskDefinition
from free_app.notifications import _split_addresses, send_run_notification


class NotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = TaskDefinition(
            id="demo",
            name="示例任务",
            package="demo.package",
            actions=(),
        )
        self.settings = {
            "email_notification": {
                "enabled": True,
                "smtp_host": "smtp.qq.com",
                "smtp_port": 465,
                "security": "ssl",
                "smtp_username": "sender@qq.com",
                "smtp_password": "authorization-code",
                "recipients": ["receiver@example.com"],
            }
        }

    @staticmethod
    def _message_body(message: object) -> str:
        if not hasattr(message, "walk"):
            return message.get_content()  # type: ignore[union-attr]
        text_parts = [
            part
            for part in message.walk()  # type: ignore[union-attr]
            if part.get_content_type() == "text/plain"
        ]
        return text_parts[0].get_payload(decode=True).decode("utf-8")

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_single_result_uses_requested_text(self, smtp_class: MagicMock) -> None:
        client = smtp_class.return_value.__enter__.return_value
        result = RunResult("demo", RunStatus.SUCCESS, 1, 3)

        self.assertTrue(send_run_notification(self.settings, result, [self.task], []))

        message = client.send_message.call_args.args[0]
        self.assertEqual(message["Subject"], "FREE")
        self.assertEqual(self._message_body(message).rstrip("\n"), "✅成功：示例任务")

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_batch_success_uses_execution_order_without_step_counts(
        self, smtp_class: MagicMock
    ) -> None:
        client = smtp_class.return_value.__enter__.return_value
        tasks = [
            TaskDefinition("first", "第一个", "first.package", ()),
            TaskDefinition("second", "第二个", "second.package", ()),
        ]
        summary = BatchRunResult(
            status=RunStatus.SUCCESS,
            results=(
                RunResult("first", RunStatus.SUCCESS, 1, 2),
                RunResult("second", RunStatus.SUCCESS, 2, 2),
            ),
            total_tasks=2,
            completed_tasks=2,
        )

        self.assertTrue(send_run_notification(self.settings, summary, tasks, []))

        message = client.send_message.call_args.args[0]
        self.assertEqual(message["Subject"], "FREE")
        body = self._message_body(message).rstrip("\n")
        self.assertEqual(
            body,
            "\n".join(
                [
                    "✅✅全部成功",
                    "成功指令：第一个，第二个",
                    "第一个",
                    "第二个",
                ]
            ),
        )
        self.assertNotIn("1/2", body)
        self.assertNotIn("- ", body)

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_batch_failures_are_listed_and_screenshots_are_failure_first(
        self, smtp_class: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            success_image = base / "success.png"
            failed_image = base / "failed.png"
            success_image.write_bytes(b"\x89PNG\r\n\x1a\nfake png")
            failed_image.write_bytes(b"\x89PNG\r\n\x1a\nfake png")
            tasks = [
                TaskDefinition("success", "成功任务", "success.package", ()),
                TaskDefinition("failed", "失败任务", "failed.package", ()),
            ]
            summary = BatchRunResult(
                status=RunStatus.FAILED,
                results=(
                    RunResult(
                        "success",
                        RunStatus.SUCCESS,
                        2,
                        2,
                        key_screenshots=(success_image,),
                    ),
                    RunResult(
                        "failed",
                        RunStatus.FAILED,
                        1,
                        2,
                        screenshot=failed_image,
                    ),
                ),
                total_tasks=2,
                completed_tasks=2,
                failed_task="failed",
            )

            client = smtp_class.return_value.__enter__.return_value
            self.assertTrue(send_run_notification(self.settings, summary, tasks, []))
            message = client.send_message.call_args.args[0]

        body = self._message_body(message).rstrip("\n")
        self.assertNotIn(str(failed_image), body)
        self.assertNotIn(str(success_image), body)
        self.assertEqual(
            body,
            "\n".join(
                [
                    "❌ 失败指令：失败任务",
                    "✅ 成功指令：成功任务",
                    "失败任务",
                    "成功任务",
                ]
            ),
        )
        image_parts = [
            part
            for part in message.walk()
            if part.get_content_type().startswith("image/")
        ]
        self.assertEqual(
            [part.get_filename() for part in image_parts],
            ["failed.png", "success.png"],
        )
        html_parts = [
            part
            for part in message.walk()
            if part.get_content_type() == "text/html"
        ]
        html_body = html_parts[0].get_payload(decode=True).decode("utf-8")
        self.assertLess(
            html_body.index("cid:free-image-1"),
            html_body.index("cid:free-image-2"),
        )

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_smtp_error_is_logged_and_does_not_raise(self, smtp_class: MagicMock) -> None:
        smtp_class.return_value.__enter__.side_effect = OSError("connection refused")
        logs: list[str] = []
        result = RunResult("demo", RunStatus.SUCCESS, 1, 1)

        self.assertFalse(send_run_notification(self.settings, result, [self.task], logs.append))
        self.assertTrue(any("邮件通知发送失败" in log for log in logs))

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_plain_security_is_no_longer_supported(self, smtp_class: MagicMock) -> None:
        settings = dict(self.settings)
        settings["email_notification"] = dict(self.settings["email_notification"])
        settings["email_notification"]["security"] = "none"
        logs: list[str] = []
        result = RunResult("demo", RunStatus.SUCCESS, 1, 1)

        self.assertFalse(
            send_run_notification(settings, result, [self.task], logs.append)
        )

        smtp_class.assert_not_called()
        self.assertTrue(any("不支持的 SMTP 安全模式: none" in log for log in logs))

    @patch("free_app.notifications.smtplib.SMTP")
    def test_starttls_security_performs_ehlo_tls_and_login(self, smtp_class: MagicMock) -> None:
        client = smtp_class.return_value.__enter__.return_value
        settings = dict(self.settings)
        settings["email_notification"] = dict(self.settings["email_notification"])
        settings["email_notification"].update({"security": "starttls", "smtp_port": 587})

        self.assertTrue(send_run_notification(settings, RunResult("demo", RunStatus.SUCCESS, 1, 1)))

        smtp_class.assert_called_once_with("smtp.qq.com", 587, timeout=20.0)
        self.assertEqual(client.ehlo.call_count, 2)
        client.starttls.assert_called_once_with()
        client.login.assert_called_once_with("sender@qq.com", "authorization-code")
        client.send_message.assert_called_once()

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_invalid_smtp_configuration_is_logged_without_connecting(
        self, smtp_class: MagicMock
    ) -> None:
        settings = dict(self.settings)
        settings["email_notification"] = dict(self.settings["email_notification"])
        settings["email_notification"]["smtp_port"] = "not-a-port"
        logs: list[str] = []

        self.assertFalse(
            send_run_notification(settings, RunResult("demo", RunStatus.SUCCESS, 1, 1), log_callback=logs.append)
        )

        smtp_class.assert_not_called()
        self.assertTrue(logs)

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_unsupported_security_mode_is_logged_without_connecting(
        self, smtp_class: MagicMock
    ) -> None:
        settings = dict(self.settings)
        settings["email_notification"] = dict(self.settings["email_notification"])
        settings["email_notification"]["security"] = "unsupported"
        logs: list[str] = []

        self.assertFalse(
            send_run_notification(settings, RunResult("demo", RunStatus.SUCCESS, 1, 1), log_callback=logs.append)
        )

        smtp_class.assert_not_called()
        self.assertTrue(logs)

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_disabled_notification_does_not_connect(self, smtp_class: MagicMock) -> None:
        settings = {"email_notification": {"enabled": False}}

        self.assertFalse(send_run_notification(settings, RunResult("demo", RunStatus.SUCCESS, 1, 1)))
        smtp_class.assert_not_called()

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_none_screenshot_level_sends_without_screenshot_text_or_images(
        self, smtp_class: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "demo.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake png")
            result = RunResult(
                "demo",
                RunStatus.SUCCESS,
                1,
                1,
                key_screenshots=(image_path,),
            )
            settings = dict(self.settings)
            settings["screenshot_save_level"] = "none"

            client = smtp_class.return_value.__enter__.return_value
            self.assertTrue(send_run_notification(settings, result, [self.task], []))
            message = client.send_message.call_args.args[0]

        self.assertFalse(message.is_multipart())
        self.assertNotIn("截图", self._message_body(message))
        self.assertEqual(list(message.iter_attachments()), [])

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_notify_on_excludes_unwanted_status(self, smtp_class: MagicMock) -> None:
        settings = dict(self.settings)
        settings["email_notification"] = dict(self.settings["email_notification"])
        settings["email_notification"]["notify_on"] = ["failed"]

        self.assertFalse(
            send_run_notification(
                settings,
                RunResult("demo", RunStatus.SUCCESS, 1, 1),
            )
        )
        smtp_class.assert_not_called()

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_incomplete_smtp_config_is_skipped(self, smtp_class: MagicMock) -> None:
        settings = {
            "email_notification": {
                "enabled": True,
                "smtp_host": "",
                "smtp_username": "",
                "smtp_password": "",
            }
        }
        logs: list[str] = []

        self.assertFalse(
            send_run_notification(
                settings,
                RunResult("demo", RunStatus.SUCCESS, 1, 1),
                log_callback=logs.append,
            )
        )
        smtp_class.assert_not_called()
        self.assertTrue(any("配置不完整" in message for message in logs))

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_attachment_read_failure_is_logged_and_skipped(
        self, smtp_class: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            screenshot_directory = Path(directory) / "screenshot.png"
            screenshot_directory.mkdir()
            result = RunResult(
                "demo",
                RunStatus.SUCCESS,
                1,
                1,
                screenshot=screenshot_directory,
            )
            logs: list[str] = []

            self.assertTrue(
                send_run_notification(
                    self.settings,
                    result,
                    [self.task],
                    logs.append,
                )
            )
            message = smtp_class.return_value.__enter__.return_value.send_message.call_args.args[0]

        self.assertTrue(message.is_multipart())
        self.assertEqual(
            [
                part
                for part in message.walk()
                if part.get_content_type().startswith("image/")
            ],
            [],
        )
        self.assertTrue(any("图片读取失败" in message for message in logs))

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_single_result_includes_config_error_block(self, smtp_class: MagicMock) -> None:
        error = TaskFileError(Path("broken.json"), "invalid")
        result = RunResult("demo", RunStatus.FAILED, 0, 1)

        self.assertTrue(
            send_run_notification(
                self.settings,
                result,
                [self.task],
                [],
                [error],
            )
        )
        message = smtp_class.return_value.__enter__.return_value.send_message.call_args.args[0]

        body = self._message_body(message)
        self.assertIn("配置错误", body)
        self.assertIn("broken.json", body)

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_batch_stopped_and_config_errors_are_formatted(
        self, smtp_class: MagicMock
    ) -> None:
        error = TaskFileError(Path("broken.json"), "invalid")
        summary = BatchRunResult(
            status=RunStatus.STOPPED,
            results=(RunResult("demo", RunStatus.STOPPED, 1, 2),),
            total_tasks=1,
            completed_tasks=1,
        )

        self.assertTrue(
            send_run_notification(
                self.settings,
                summary,
                [self.task],
                [],
                [error],
            )
        )
        message = smtp_class.return_value.__enter__.return_value.send_message.call_args.args[0]

        body = self._message_body(message)
        self.assertIn("已停止", body)
        self.assertIn("配置错误", body)

    def test_split_addresses_accepts_string_and_list(self) -> None:
        self.assertEqual(
            _split_addresses("a@example.com; b@example.com"),
            ["a@example.com", "b@example.com"],
        )
        self.assertEqual(
            _split_addresses(["a@example.com", " b@example.com "]),
            ["a@example.com", "b@example.com"],
        )


if __name__ == "__main__":
    unittest.main()
