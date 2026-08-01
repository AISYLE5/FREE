from __future__ import annotations

import unittest

from free_app.logging_utils import format_log_line, should_write_log_line


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

    def test_all_level_writes_every_line(self) -> None:
        self.assertTrue(should_write_log_line("all", "OCR 点击候选: ['领取']"))
        self.assertTrue(should_write_log_line("all", "普通调试信息"))
        self.assertTrue(should_write_log_line("all", "截图[before]: screenshots/xxx.png"))
        self.assertTrue(should_write_log_line("all", "截图[after]: screenshots/xxx.png"))
        self.assertTrue(should_write_log_line("all", "关键页面截图: screenshots/xxx.png"))
        self.assertTrue(should_write_log_line("all", "失败截图: screenshots/xxx.png"))

    def test_none_level_writes_nothing(self) -> None:
        self.assertFalse(should_write_log_line("none", "开始任务 [1/1]: 示例"))
        self.assertFalse(should_write_log_line("none", "OCR 点击候选: []"))

    def test_summary_level_keeps_milestones_and_drops_noise(self) -> None:
        self.assertTrue(should_write_log_line("summary", "开始任务 [1/5]: 毛怪俱乐部"))
        self.assertTrue(should_write_log_line("summary", "任务结果 [1/5] 毛怪俱乐部: success"))
        self.assertTrue(should_write_log_line("summary", "关键页面截图: screenshots/xxx.png"))
        self.assertTrue(should_write_log_line("summary", "任务失败: OCR 未识别到文案"))
        self.assertTrue(should_write_log_line("summary", "任务异常: 未知错误"))
        self.assertTrue(should_write_log_line("summary", "设备准备失败: 设备不可用"))
        self.assertTrue(should_write_log_line("summary", "批量任务准备失败: 设备不可用"))
        self.assertTrue(should_write_log_line("summary", "App 清理失败: 关闭失败"))
        self.assertTrue(should_write_log_line("summary", "关闭 App 进程失败: demo: 失败"))
        self.assertTrue(
            should_write_log_line("summary", "开始关闭 App 进程: tv.danmaku.bili, com.tencent.mobileqq")
        )
        self.assertTrue(should_write_log_line("summary", "收到停止信号: 2，等待当前 ADB 动作结束"))
        self.assertTrue(should_write_log_line("summary", "设备准备已停止，不再启动任务"))
        self.assertTrue(should_write_log_line("summary", "邮件通知已发送: a@b.com"))
        self.assertFalse(should_write_log_line("summary", "OCR 点击候选: ['领取', '已领取']"))
        self.assertFalse(should_write_log_line("summary", "ADB tap: (540, 960)"))
        self.assertTrue(should_write_log_line("summary", "动作失败，将重试 (1/2): OCR 超时"))
        self.assertTrue(should_write_log_line("summary", "动作最终失败: OCR 超时"))
        self.assertFalse(should_write_log_line("summary", "已关闭 App 进程: tv.danmaku.bili"))
        self.assertFalse(should_write_log_line("summary", "任务结束，3 秒后开始关闭 App 进程"))
        self.assertFalse(should_write_log_line("summary", "OCR 点击检测失败，继续重试: 超时"))
        self.assertFalse(should_write_log_line("summary", "截图[before]: screenshots/xxx.png"))
        self.assertFalse(should_write_log_line("summary", "截图[after]: screenshots/xxx.png"))


if __name__ == "__main__":
    unittest.main()
