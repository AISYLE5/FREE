from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from free_app.task_completions import (
    COMPLETIONS_FILE_NAME,
    completed_task_ids,
    completion_moment,
    completions_path,
    format_completion,
    is_completed_on,
    load_completions,
    mark_completed,
    parse_completion,
    reset_completion,
    reset_completions,
    sanitize_completions,
    save_completions,
)


class TaskCompletionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.settings_path = self.base / "config" / "settings.json"
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- 路径/格式

    def test_completions_path_sits_next_to_settings_file(self) -> None:
        path = completions_path(self.settings_path)

        self.assertEqual(path.name, COMPLETIONS_FILE_NAME)
        self.assertEqual(path.parent, self.settings_path.parent)

    def test_completion_round_trips_through_storage_format(self) -> None:
        moment = datetime(2026, 10, 10, 21, 32, 5)

        text = format_completion(moment)

        self.assertEqual(text, "2026-10-10 21:32:05")
        self.assertEqual(parse_completion(text), moment)

    def test_parse_completion_rejects_unparsable_values(self) -> None:
        for value in (
            None,
            "",
            "   ",
            123,
            [],
            {},
            "not-a-date",
            "2026-10-10",  # 缺少时间部分
            "2026-13-45 99:99:99",
            True,
        ):
            with self.subTest(value=value):
                self.assertIsNone(parse_completion(value))

    def test_parse_completion_accepts_surrounding_whitespace(self) -> None:
        self.assertEqual(
            parse_completion("  2026-10-10 08:09:10  "),
            datetime(2026, 10, 10, 8, 9, 10),
        )

    # ------------------------------------------------------------------ 清洗

    def test_sanitize_completions_drops_bad_entries_and_normalizes(self) -> None:
        cleaned = sanitize_completions(
            {
                "ok": "2026-10-10 21:32:05",
                "padded": "  2026-10-10 01:02:03  ",
                "bad_date": "yesterday",
                "empty": "",
                "null": None,
                "number": 12,
                "": "2026-10-10 21:32:05",
                "   ": "2026-10-10 21:32:05",
            }
        )

        self.assertEqual(
            cleaned,
            {
                "ok": "2026-10-10 21:32:05",
                "padded": "2026-10-10 01:02:03",
            },
        )

    def test_sanitize_completions_rejects_non_mapping_input(self) -> None:
        for value in (None, [], "text", 5, True):
            with self.subTest(value=value):
                self.assertEqual(sanitize_completions(value), {})

    # ------------------------------------------------------------ 读写与容错

    def test_load_completions_returns_empty_when_file_is_missing(self) -> None:
        self.assertEqual(load_completions(self.settings_path), {})

    def test_load_completions_tolerates_corrupt_or_non_object_files(self) -> None:
        for payload in ("{ not json", "[]", '"text"', "null", "12"):
            with self.subTest(payload=payload):
                self.settings_path.write_text(payload, encoding="utf-8")
                self.assertEqual(load_completions(self.settings_path), {})

    def test_load_completions_reads_saved_values(self) -> None:
        save_completions(
            self.settings_path,
            {"hanserclub": "2026-10-10 21:32:05", "broken": "nope"},
        )
        self.assertEqual(
            json.loads(self.settings_path.read_text(encoding="utf-8")),
            {"broken": "nope", "hanserclub": "2026-10-10 21:32:05"},
        )

        self.assertEqual(
            load_completions(self.settings_path),
            {"hanserclub": "2026-10-10 21:32:05"},
        )

    def test_save_completions_writes_sorted_keys(self) -> None:
        save_completions(
            self.settings_path,
            {"zulu": "2026-10-10 00:00:01", "alpha": "2026-10-10 00:00:02"},
        )

        text = self.settings_path.read_text(encoding="utf-8")
        self.assertLess(text.index("alpha"), text.index("zulu"))

    def test_mark_completed_merges_with_existing_records(self) -> None:
        save_completions(self.settings_path, {"first": "2026-10-01 10:00:00"})

        result = mark_completed(
            self.settings_path, "second", datetime(2026, 10, 10, 21, 32, 5)
        )

        self.assertEqual(
            result,
            {
                "first": "2026-10-01 10:00:00",
                "second": "2026-10-10 21:32:05",
            },
        )
        self.assertEqual(load_completions(self.settings_path), result)

    def test_mark_completed_uses_now_when_moment_is_omitted(self) -> None:
        before = datetime.now().replace(microsecond=0)

        result = mark_completed(self.settings_path, "demo")

        moment = parse_completion(result["demo"])
        self.assertIsNotNone(moment)
        assert moment is not None
        # 存储精度为秒，因此用秒级窗口断言。
        self.assertLessEqual(before, moment)
        self.assertLessEqual(moment, datetime.now())

    def test_mark_completed_overwrites_previous_moment(self) -> None:
        mark_completed(self.settings_path, "demo", datetime(2026, 10, 9, 1, 2, 3))

        result = mark_completed(
            self.settings_path, "demo", datetime(2026, 10, 10, 4, 5, 6)
        )

        self.assertEqual(result, {"demo": "2026-10-10 04:05:06"})

    # ------------------------------------------------------------------ 重置

    def test_reset_completion_removes_only_the_named_task(self) -> None:
        save_completions(
            self.settings_path,
            {"keep": "2026-10-10 01:00:00", "drop": "2026-10-10 02:00:00"},
        )

        result = reset_completion(self.settings_path, "drop")

        self.assertEqual(result, {"keep": "2026-10-10 01:00:00"})
        self.assertEqual(load_completions(self.settings_path), result)

    def test_reset_completion_is_a_noop_for_unknown_task(self) -> None:
        save_completions(self.settings_path, {"keep": "2026-10-10 01:00:00"})

        result = reset_completion(self.settings_path, "absent")

        self.assertEqual(result, {"keep": "2026-10-10 01:00:00"})
        self.assertEqual(load_completions(self.settings_path), result)

    def test_reset_completions_clears_everything(self) -> None:
        save_completions(self.settings_path, {"a": "2026-10-10 01:00:00"})

        self.assertEqual(reset_completions(self.settings_path), {})
        self.assertEqual(load_completions(self.settings_path), {})

    # -------------------------------------------------------------- 今日判定

    def test_completion_moment_reads_valid_records_only(self) -> None:
        completions = {"ok": "2026-10-10 21:32:05", "bad": "nope"}

        self.assertEqual(
            completion_moment(completions, "ok"), datetime(2026, 10, 10, 21, 32, 5)
        )
        self.assertIsNone(completion_moment(completions, "bad"))
        self.assertIsNone(completion_moment(completions, "absent"))

    def test_is_completed_on_compares_calendar_date_only(self) -> None:
        day = date(2026, 10, 10)
        completions = {
            "same_day_early": "2026-10-10 00:00:00",
            "same_day_late": "2026-10-10 23:59:59",
            "yesterday": "2026-10-09 23:59:59",
            "tomorrow": "2026-10-11 00:00:00",
        }

        for task_id in ("same_day_early", "same_day_late"):
            with self.subTest(task_id=task_id):
                self.assertTrue(is_completed_on(completions, task_id, day))
        for task_id in ("yesterday", "tomorrow", "absent"):
            with self.subTest(task_id=task_id):
                self.assertFalse(is_completed_on(completions, task_id, day))

    def test_is_completed_on_defaults_to_today(self) -> None:
        today = datetime.now()
        yesterday = today - timedelta(days=1)
        completions = {
            "today": format_completion(today),
            "yesterday": format_completion(yesterday),
        }

        self.assertTrue(is_completed_on(completions, "today"))
        # 跨天自动视为未完成：昨天的记录不算今天完成。
        self.assertFalse(is_completed_on(completions, "yesterday"))

    def test_completed_task_ids_keeps_order_and_dedupes(self) -> None:
        day = date(2026, 10, 10)
        completions = {
            "a": "2026-10-10 01:00:00",
            "c": "2026-10-10 03:00:00",
        }

        completed = completed_task_ids(
            completions, ["c", "b", "a", "b", "c"], day
        )

        self.assertEqual(completed, ["c", "a"])

    def test_completed_task_ids_returns_empty_without_records(self) -> None:
        self.assertEqual(completed_task_ids({}, ["a", "b"]), [])


if __name__ == "__main__":
    unittest.main()
