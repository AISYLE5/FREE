from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from free_app.config import TaskFileError
from free_app.models import BatchRunResult, RunResult, RunStatus, TaskDefinition
from free_app.notifications import (
    _task_screenshot_blocks,
    send_run_notification,
)


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
                "smtp_security": "ssl",
                "smtp_username": "sender@qq.com",
                "smtp_password": "authorization-code",
                "recipients": ["receiver@example.com"],
            }
        }

    def _with_email(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """返回 ``email_notification`` 块已具有清洗后形态的设置。"""

        email = dict(self.settings["email_notification"])
        email.update(overrides or {})
        settings = dict(self.settings)
        settings["email_notification"] = email
        return settings

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
            "✅✅全部成功\n成功指令：第一个，第二个\n第一个\n第二个",
        )
        self.assertNotIn("1/2", body)
        self.assertNotIn("- ", body)

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_batch_prepare_failure_includes_error_in_body(
        self, smtp_class: MagicMock
    ) -> None:
        client = smtp_class.return_value.__enter__.return_value
        tasks = [TaskDefinition("demo", "示例任务", "demo.package", ())]
        summary = BatchRunResult(
            status=RunStatus.FAILED,
            results=(),
            error="等待 MuMu ADB 设备超时",
            total_tasks=1,
            completed_tasks=0,
        )

        self.assertTrue(send_run_notification(self.settings, summary, tasks, []))

        message = client.send_message.call_args.args[0]
        body = self._message_body(message).rstrip("\n")
        self.assertIn("等待 MuMu ADB 设备超时", body)

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_notify_on_empty_list_sends_nothing(self, smtp_class: MagicMock) -> None:
        settings = self._with_email({"notify_on": []})

        self.assertFalse(
            send_run_notification(settings, RunResult("demo", RunStatus.SUCCESS, 1, 1))
        )
        smtp_class.assert_not_called()

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
            "❌ 失败指令：失败任务\n✅ 成功指令：成功任务\n失败任务\n失败原因：未知原因\n成功任务",
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
            part for part in message.walk() if part.get_content_type() == "text/html"
        ]
        html_body = html_parts[0].get_payload(decode=True).decode("utf-8")
        self.assertLess(
            html_body.index("cid:free-image-1"),
            html_body.index("cid:free-image-2"),
        )

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_smtp_error_is_logged_and_does_not_raise(
        self, smtp_class: MagicMock
    ) -> None:
        smtp_class.return_value.__enter__.side_effect = OSError("connection refused")
        logs: list[str] = []
        result = RunResult("demo", RunStatus.SUCCESS, 1, 1)

        self.assertFalse(
            send_run_notification(self.settings, result, [self.task], logs.append)
        )
        self.assertTrue(any("邮件通知发送失败" in log for log in logs))

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_plain_security_is_no_longer_supported(self, smtp_class: MagicMock) -> None:
        settings = self._with_email({"smtp_security": "none"})
        logs: list[str] = []
        result = RunResult("demo", RunStatus.SUCCESS, 1, 1)

        self.assertFalse(
            send_run_notification(settings, result, [self.task], logs.append)
        )

        smtp_class.assert_not_called()
        self.assertTrue(any("不支持的 SMTP 安全模式: none" in log for log in logs))

    @patch("free_app.notifications.smtplib.SMTP")
    def test_starttls_security_performs_ehlo_tls_and_login(
        self, smtp_class: MagicMock
    ) -> None:
        client = smtp_class.return_value.__enter__.return_value
        settings = self._with_email({"smtp_security": "starttls", "smtp_port": 587})

        self.assertTrue(
            send_run_notification(settings, RunResult("demo", RunStatus.SUCCESS, 1, 1))
        )

        smtp_class.assert_called_once_with("smtp.qq.com", 587, timeout=20.0)
        self.assertEqual(client.ehlo.call_count, 2)
        client.starttls.assert_called_once_with()
        client.login.assert_called_once_with("sender@qq.com", "authorization-code")
        client.send_message.assert_called_once()

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_invalid_smtp_configuration_is_logged_without_connecting(
        self, smtp_class: MagicMock
    ) -> None:
        settings = self._with_email({"recipients": []})
        logs: list[str] = []

        self.assertFalse(
            send_run_notification(
                settings,
                RunResult("demo", RunStatus.SUCCESS, 1, 1),
                log_callback=logs.append,
            )
        )

        smtp_class.assert_not_called()
        self.assertTrue(logs)

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_unsupported_security_mode_is_logged_without_connecting(
        self, smtp_class: MagicMock
    ) -> None:
        settings = self._with_email({"smtp_security": "unsupported"})
        logs: list[str] = []

        self.assertFalse(
            send_run_notification(
                settings,
                RunResult("demo", RunStatus.SUCCESS, 1, 1),
                log_callback=logs.append,
            )
        )

        smtp_class.assert_not_called()
        self.assertTrue(
            any("不支持的 SMTP 安全模式: unsupported" in log for log in logs), logs
        )

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_disabled_notification_does_not_connect(
        self, smtp_class: MagicMock
    ) -> None:
        settings = {"email_notification": {"enabled": False}}

        self.assertFalse(
            send_run_notification(settings, RunResult("demo", RunStatus.SUCCESS, 1, 1))
        )
        smtp_class.assert_not_called()

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_missing_email_block_sends_nothing(self, smtp_class: MagicMock) -> None:
        for settings in ({}, {"email_notification": {}}):
            with self.subTest(settings=settings):
                self.assertFalse(
                    send_run_notification(
                        settings, RunResult("demo", RunStatus.SUCCESS, 1, 1)
                    )
                )
        smtp_class.assert_not_called()

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_ssl_connection_uses_cleaned_numeric_settings(
        self, smtp_class: MagicMock
    ) -> None:
        # 信任 config._sanitize_email：端口/超时已是数字，这里不再转换。
        settings = self._with_email({"smtp_port": 465, "smtp_timeout_seconds": 25})
        client = smtp_class.return_value.__enter__.return_value

        self.assertTrue(
            send_run_notification(settings, RunResult("demo", RunStatus.SUCCESS, 1, 1))
        )

        smtp_class.assert_called_once_with("smtp.qq.com", 465, timeout=25.0)
        client.login.assert_called_once_with("sender@qq.com", "authorization-code")
        client.send_message.assert_called_once()

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_recipients_are_taken_verbatim_from_the_list(
        self, smtp_class: MagicMock
    ) -> None:
        settings = self._with_email({"recipients": ["a@example.com", "b@example.com"]})
        client = smtp_class.return_value.__enter__.return_value

        self.assertTrue(
            send_run_notification(settings, RunResult("demo", RunStatus.SUCCESS, 1, 1))
        )

        message = client.send_message.call_args.args[0]
        self.assertEqual(message["To"], "a@example.com, b@example.com")
        self.assertEqual(message["From"], "sender@qq.com")

    def test_non_list_recipients_raises_instead_of_char_splitting(self) -> None:
        # 消费点断言：绕过清洗层的 str recipients 绝不被拆成单字符收件人。
        settings = self._with_email({"recipients": "a@example.com"})
        with self.assertRaises(TypeError):
            send_run_notification(settings, RunResult("demo", RunStatus.SUCCESS, 1, 1))

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_subject_prefix_comes_from_configuration(
        self, smtp_class: MagicMock
    ) -> None:
        settings = self._with_email({"subject_prefix": "FREE 自动化"})
        client = smtp_class.return_value.__enter__.return_value

        self.assertTrue(
            send_run_notification(settings, RunResult("demo", RunStatus.SUCCESS, 1, 1))
        )

        message = client.send_message.call_args.args[0]
        self.assertEqual(message["Subject"], "FREE 自动化")

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_notify_on_matches_the_run_status_exactly(
        self, smtp_class: MagicMock
    ) -> None:
        settings = self._with_email({"notify_on": ["failed", "stopped"]})

        self.assertFalse(
            send_run_notification(settings, RunResult("demo", RunStatus.SUCCESS, 1, 1))
        )
        smtp_class.assert_not_called()

        self.assertTrue(
            send_run_notification(settings, RunResult("demo", RunStatus.FAILED, 1, 1))
        )
        smtp_class.assert_called_once()

    def test_email_without_screenshot_paths_has_no_screenshot_blocks(self) -> None:
        result = RunResult("demo", RunStatus.SUCCESS, 1, 1)
        self.assertEqual(_task_screenshot_blocks(result), [])

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_incomplete_smtp_config_is_skipped(self, smtp_class: MagicMock) -> None:
        # 清洗后仍会出现的"配置不完整"：用户名/密码/收件人为空。
        settings = self._with_email(
            {"smtp_username": "", "smtp_password": "", "recipients": []}
        )
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
            message = smtp_class.return_value.__enter__.return_value.send_message.call_args.args[
                0
            ]

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
    def test_single_result_includes_config_error_block(
        self, smtp_class: MagicMock
    ) -> None:
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
        message = (
            smtp_class.return_value.__enter__.return_value.send_message.call_args.args[
                0
            ]
        )

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
        message = (
            smtp_class.return_value.__enter__.return_value.send_message.call_args.args[
                0
            ]
        )

        body = self._message_body(message)
        self.assertIn("已停止", body)
        self.assertIn("配置错误", body)

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_batch_stopped_includes_uncompleted_tasks(
        self, smtp_class: MagicMock
    ) -> None:
        tasks = [
            TaskDefinition("first", "第一个", "first.package", ()),
            TaskDefinition("second", "第二个", "second.package", ()),
            TaskDefinition("third", "第三个", "third.package", ()),
        ]
        summary = BatchRunResult(
            status=RunStatus.STOPPED,
            results=(
                RunResult("first", RunStatus.SUCCESS, 1, 1),
                RunResult("second", RunStatus.STOPPED, 1, 2),
            ),
            total_tasks=3,
            completed_tasks=2,
        )

        client = smtp_class.return_value.__enter__.return_value
        self.assertTrue(send_run_notification(self.settings, summary, tasks, []))
        message = client.send_message.call_args.args[0]
        body = self._message_body(message).rstrip("\n")

        self.assertIn("⏸ 未完成指令：第三个", body)
        self.assertIn("第一个", body)
        self.assertIn("第二个", body)

    @patch("free_app.notifications.smtplib.SMTP_SSL")
    def test_single_failed_result_includes_reason_and_screenshot(
        self, smtp_class: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            screenshot = Path(directory) / "failed.png"
            screenshot.write_bytes(b"\x89PNG\r\n\x1a\nfake png")
            result = RunResult(
                "demo",
                RunStatus.FAILED,
                1,
                2,
                failed_step="点击领取",
                error="检测超时",
                screenshot=screenshot,
            )

            client = smtp_class.return_value.__enter__.return_value
            self.assertTrue(
                send_run_notification(self.settings, result, [self.task], [])
            )
            message = client.send_message.call_args.args[0]

        body = self._message_body(message).rstrip("\n")
        self.assertIn("❌失败：示例任务", body)
        self.assertIn("失败原因：检测超时", body)
        image_parts = [
            part
            for part in message.walk()
            if part.get_content_type().startswith("image/")
        ]
        self.assertEqual([part.get_filename() for part in image_parts], ["failed.png"])


if __name__ == "__main__":
    unittest.main()
