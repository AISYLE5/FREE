from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from free_app.pruning import clear_output_files, prune_files
from free_app.trash import TrashError, send_to_recycle_bin


class PruningTests(unittest.TestCase):
    def test_permanent_removes_oldest_beyond_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            files = []
            for index in range(5):
                path = target / f"file{index}.txt"
                path.write_text(str(index), encoding="utf-8")
                files.append(path)
            for index, path in enumerate(files):
                os.utime(path, (index, index))

            removed = prune_files(target, 2, "permanent")

            self.assertEqual(removed, 3)
            self.assertEqual(
                sorted(path.name for path in target.iterdir()),
                ["file3.txt", "file4.txt"],
            )

    def test_recycle_sends_oldest_to_trash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            files = []
            for index in range(4):
                path = target / f"f{index}.txt"
                path.write_text(str(index), encoding="utf-8")
                files.append(path)
            for index, path in enumerate(files):
                os.utime(path, (index, index))

            def fake_trash(path: Path, mode: str) -> None:
                path.unlink()

            with patch("free_app.pruning.remove_path", side_effect=fake_trash) as trash:
                removed = prune_files(target, 2, "recycle")

            self.assertEqual(removed, 2)
            self.assertEqual(trash.call_count, 2)
            self.assertEqual(
                sorted(path.name for path in target.iterdir()), ["f2.txt", "f3.txt"]
            )

    def test_zero_limit_keeps_everything(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for index in range(4):
                (target / f"f{index}.txt").write_text(str(index), encoding="utf-8")

            removed = prune_files(target, 0, "permanent")

            self.assertEqual(removed, 0)
            self.assertEqual(len(list(target.iterdir())), 4)

    def test_prune_files_skips_missing_directory_and_under_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            self.assertEqual(prune_files(missing, 5, "permanent"), 0)

            target = Path(directory)
            (target / "a.log").write_text("a", encoding="utf-8")
            self.assertEqual(prune_files(target, 5, "permanent"), 0)

    def test_prune_files_logs_failed_cleanup_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for index in range(3):
                path = target / f"f{index}.txt"
                path.write_text(str(index), encoding="utf-8")
            logs: list[str] = []

            def failing_trash(path: Path, mode: str) -> None:
                if path.name == "f0.txt":
                    raise TrashError("recycle failed")
                path.unlink()

            with patch(
                "free_app.pruning.remove_path",
                side_effect=failing_trash,
            ):
                removed = prune_files(target, 1, "recycle", logs.append)

        self.assertEqual(removed, 1)
        self.assertTrue(any("清理旧文件失败" in message for message in logs))

    def test_recycle_bin_rejects_non_windows_platform(self) -> None:
        path = Path("C:/unused")
        with patch("free_app.trash.os.name", "posix"):
            with self.assertRaises(TrashError):
                send_to_recycle_bin(path)

    def test_recycle_bin_reports_native_success_and_failure(self) -> None:
        with patch(
            "free_app.trash.ctypes.windll.shell32.SHFileOperationW", return_value=0
        ) as operation:
            send_to_recycle_bin(Path("C:/unused"))
        operation.assert_called_once()

        with patch(
            "free_app.trash.ctypes.windll.shell32.SHFileOperationW", return_value=5
        ):
            with self.assertRaises(TrashError):
                send_to_recycle_bin(Path("C:/unused"))

    def test_clear_output_files_permanent_removes_everything(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "nested").mkdir()
            (target / "a.log").write_text("a", encoding="utf-8")
            (target / "nested" / "b.log").write_text("b", encoding="utf-8")

            removed = clear_output_files(target, "permanent")

            self.assertEqual(removed, 2)
            self.assertEqual([path for path in target.rglob("*") if path.is_file()], [])

    def test_clear_output_files_recycle_sends_files_to_trash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "a.log").write_text("a", encoding="utf-8")
            (target / "b.log").write_text("b", encoding="utf-8")

            def fake_trash(path: Path, mode: str) -> None:
                path.unlink()

            with patch(
                "free_app.pruning.remove_path",
                side_effect=fake_trash,
            ) as trash:
                removed = clear_output_files(target, "recycle")

            self.assertEqual(removed, 2)
            self.assertEqual(trash.call_count, 2)
            self.assertEqual(list(target.iterdir()), [])

    def test_clear_output_files_skips_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                clear_output_files(Path(directory) / "missing", "permanent"), 0
            )

    def test_clear_output_files_logs_failed_cleanup_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "a.log").write_text("a", encoding="utf-8")
            (target / "b.log").write_text("b", encoding="utf-8")
            logs: list[str] = []

            def failing_trash(path: Path, mode: str) -> None:
                if path.name == "a.log":
                    raise OSError("remove failed")
                path.unlink()

            with patch(
                "free_app.pruning.remove_path",
                side_effect=failing_trash,
            ):
                removed = clear_output_files(target, "recycle", logs.append)

        self.assertEqual(removed, 1)
        self.assertTrue(any("清理文件失败" in message for message in logs))


if __name__ == "__main__":
    unittest.main()
