from __future__ import annotations

import unittest

from free_app.logging_utils import format_log_line


class LoggingUtilsTests(unittest.TestCase):
    def test_format_log_line_preserves_existing_timestamp(self) -> None:
        self.assertEqual(
            format_log_line("[12:34:56] worker message"),
            "[12:34:56] worker message",
        )

    def test_format_log_line_preserves_fractional_timestamp(self) -> None:
        self.assertEqual(
            format_log_line("[12:34:56.123456] worker message"),
            "[12:34:56.123456] worker message",
        )

    def test_format_log_line_adds_timestamp_to_plain_message(self) -> None:
        from datetime import datetime

        now = datetime(2026, 8, 7, 9, 8, 7)
        self.assertEqual(
            format_log_line("普通消息", now=now),
            "[09:08:07] 普通消息",
        )

    def test_format_log_line_keeps_context_prefix_but_adds_timestamp(self) -> None:
        from datetime import datetime

        now = datetime(2026, 8, 7, 9, 8, 7)
        self.assertEqual(
            format_log_line("[hanserclub] 第 1/6 步: stop", now=now),
            "[09:08:07] [hanserclub] 第 1/6 步: stop",
        )


if __name__ == "__main__":
    unittest.main()
