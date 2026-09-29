from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from free_app.lock import InstanceLock, LockError


class InstanceLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_acquire_and_release_round_trip(self) -> None:
        lock = InstanceLock("1", self.directory)
        lock.acquire()
        self.assertTrue(lock.path.exists())
        lock.release()
        self.assertFalse(lock.path.exists())

    def test_second_lock_on_same_index_is_rejected(self) -> None:
        """本进程已持锁时再次获取必须失败，避免同一实例被两个 worker 使用。"""

        holder = InstanceLock("1", self.directory)
        holder.acquire()
        other = InstanceLock("1", self.directory)
        with self.assertRaises(LockError):
            other.acquire()
        holder.release()

    def test_different_indices_do_not_conflict(self) -> None:
        first = InstanceLock("0", self.directory)
        second = InstanceLock("1", self.directory)
        first.acquire()
        second.acquire()
        self.assertTrue(first.path.exists())
        self.assertTrue(second.path.exists())
        first.release()
        second.release()

    def test_stale_lock_from_dead_process_is_reclaimed(self) -> None:
        """锁文件残留但无进程持有时必须可重新获取。"""

        lock = InstanceLock("1", self.directory)
        lock.path.write_text("999999999", encoding="ascii")
        lock.acquire()
        self.assertTrue(lock.path.exists())
        lock.release()

    def test_lock_with_garbage_content_is_reclaimed(self) -> None:
        lock = InstanceLock("1", self.directory)
        lock.path.write_text("not-a-pid", encoding="ascii")
        lock.acquire()
        lock.release()

    def test_release_is_idempotent(self) -> None:
        lock = InstanceLock("1", self.directory)
        lock.acquire()
        lock.release()
        lock.release()

    def test_context_manager_releases_on_exit(self) -> None:
        with InstanceLock("1", self.directory) as lock:
            self.assertTrue(lock.path.exists())
        self.assertFalse(lock.path.exists())

    def test_lock_records_current_pid_in_owner_file(self) -> None:
        """PID 写在独立文件：msvcrt 锁定期间锁文件本身不可读。"""

        lock = InstanceLock("1", self.directory)
        lock.acquire()
        self.assertEqual(
            str(os.getpid()), lock.owner_path.read_text(encoding="ascii")
        )
        lock.release()
        self.assertFalse(lock.owner_path.exists())

    def test_holder_pid_is_reported_when_lock_is_contended(self) -> None:
        """争用时能把持锁进程的 PID 报给用户，便于定位。"""

        holder = InstanceLock("1", self.directory)
        holder.acquire()
        other = InstanceLock("1", self.directory)
        with self.assertRaises(LockError) as caught:
            other.acquire()
        self.assertIn(str(os.getpid()), str(caught.exception))
        holder.release()

    def test_lock_directory_is_created_on_demand(self) -> None:
        nested = self.directory / "missing" / "nested"
        lock = InstanceLock("1", nested)
        lock.acquire()
        self.assertTrue(lock.path.exists())
        lock.release()


class CrossProcessLockTests(unittest.TestCase):
    """真正跨进程验证：同一进程内的重复获取无法暴露锁的全部语义。"""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def _spawn(self, body: str) -> subprocess.Popen[str]:
        script = (
            "import sys, time\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
            "from free_app.lock import InstanceLock, LockError\n"
            "from pathlib import Path\n"
            f"lock = InstanceLock('x', Path({str(self.directory)!r}))\n" + body
        )
        return subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_second_process_is_rejected_while_lock_is_held(self) -> None:
        holder = self._spawn("lock.acquire()\nprint('held', flush=True)\ntime.sleep(30)\n")
        try:
            self.assertEqual("held", holder.stdout.readline().strip())
            waiter = self._spawn(
                "time.sleep(0.3)\n"
                "try:\n"
                "    lock.acquire()\n"
                "    print('acquired')\n"
                "except LockError:\n"
                "    print('rejected')\n"
            )
            out, _err = waiter.communicate(timeout=30)
            self.assertEqual("rejected", out.strip())
        finally:
            holder.kill()
            holder.wait()

    def test_killed_holder_does_not_leave_unrecoverable_lock(self) -> None:
        """持锁进程被强杀后，锁必须能被下一个进程回收。

        这曾是真实缺陷：早期实现只写 PID 文件不做系统级加锁，
        崩溃后残留文件会让后续启动永久失败。
        """

        holder = self._spawn("lock.acquire()\nprint('held', flush=True)\ntime.sleep(30)\n")
        self.assertEqual("held", holder.stdout.readline().strip())
        holder.kill()
        holder.wait()

        reclaimer = self._spawn(
            "try:\n"
            "    lock.acquire()\n"
            "    lock.release()\n"
            "    print('reclaimed')\n"
            "except LockError:\n"
            "    print('stuck')\n"
        )
        out, _err = reclaimer.communicate(timeout=30)
        self.assertEqual("reclaimed", out.strip())
