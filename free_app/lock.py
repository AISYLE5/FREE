"""基于文件锁的进程级互斥，防止多个 FREE 进程操作同一个 MuMu 实例。

事故背景：两个 FREE 进程先后连上同一个 ``vmindex`` 的 ADB 端口。
先跑完的进程会执行 ``control --vmindex N shutdown`` 与 ``main close``，
后一个进程的 dump 随即连续秒失败并报 ``device offline``，
整批任务在「准备连接设备」阶段无意义地重试到耗尽。

``vmindex`` 是互斥粒度：不同实例可以并行，同一实例必须串行。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Self

from .helpers import LogCallback, noop_log

# 锁文件目录：与 MuMu 安装目录无关，放在用户级临时目录，
# 避免多个 FREE 安装路径差异导致锁不互斥。
_LOCK_DIRECTORY_NAME = "free-mumu-locks"


class LockError(RuntimeError):
    pass


class InstanceLock:
    """一个 ``vmindex`` 的独占锁。

    锁通过 ``O_CREAT | O_EXCL`` 创建文件获得，进程退出或崩溃后
    由操作系统回收文件句柄；锁文件本身保留，因此创建时要先判断
    持有者是否仍然存活，避免把陈旧的锁文件当成占用。
    """

    def __init__(self, vmindex: str, lock_directory: Path | None = None):
        self.vmindex = str(vmindex)
        self.lock_directory = lock_directory or default_lock_directory()
        self.path = self.lock_directory / f"vmindex_{self.vmindex}.lock"
        # 持锁者 PID 写在独立文件里：msvcrt 锁定期间锁文件本身不可读，
        # 因此提示信息无法从锁文件内容取得。
        self.owner_path = self.lock_directory / f"vmindex_{self.vmindex}.owner"
        self._handle: int | None = None

    def acquire(self, log_callback: LogCallback | None = None) -> None:
        """获取锁；已被其他存活进程持有时抛出 :class:`LockError`。

        判定分两层：先看锁文件里的 PID 是否仍存活，再用 ``msvcrt``
        对该文件加独占锁。后者由操作系统在进程退出时自动释放，
        因此即使进程被强杀、PID 被复用，也不会留下无法回收的锁。
        """

        log = noop_log(log_callback)
        self.lock_directory.mkdir(parents=True, exist_ok=True)

        owner = self._live_owner()
        if owner is not None:
            holder = f"（PID {owner}）" if owner else ""
            raise LockError(
                f"MuMu 实例 {self.vmindex} 正被另一个 FREE 进程{holder}使用。"
                f"同一实例不允许并发操作：先结束的进程会关闭实例，"
                f"导致后一个进程的整批任务失败。"
                f"请等待该进程结束，或在设置中改用其它实例编号。"
            )

        self._open_and_lock()
        try:
            self.owner_path.write_text(str(os.getpid()), encoding="ascii")
        except OSError:
            # 写不进 PID 只影响提示信息的详细程度，不影响互斥本身。
            pass
        log(f"已锁定 MuMu 实例 {self.vmindex}（PID {os.getpid()}）")

    def _open_and_lock(self) -> None:
        """打开锁文件并申请系统级独占锁。"""

        try:
            self._handle = os.open(str(self.path), os.O_CREAT | os.O_RDWR)
        except OSError as exc:
            raise LockError(f"无法创建锁文件 {self.path}: {exc}") from exc
        try:
            _lock_file(self._handle)
        except OSError as exc:
            os.close(self._handle)
            self._handle = None
            raise LockError(
                f"MuMu 实例 {self.vmindex} 的锁被其他进程持有"
            ) from exc

    def release(self) -> None:
        """释放锁；重复调用安全。"""

        if self._handle is not None:
            try:
                _unlock_file(self._handle)
            except OSError:
                pass
            try:
                os.close(self._handle)
            except OSError:
                pass
            self._handle = None
        try:
            self.path.unlink()
        except OSError:
            pass
        try:
            self.owner_path.unlink()
        except OSError:
            pass

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()

    def _read_owner(self) -> int | None:
        try:
            text = self.owner_path.read_text(encoding="ascii").strip()
        except OSError:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    def _live_owner(self) -> int | None:
        """返回仍在运行的锁持有者 PID；无人持有时返回 ``None``。

        以系统级文件锁为准：能立刻加上锁说明没有其它进程持有，
        锁文件是否残留都不影响判定。
        """

        if not self.path.exists():
            return None
        probe = None
        try:
            probe = os.open(str(self.path), os.O_RDWR)
        except OSError:
            # 锁文件存在但打不开：视为陈旧。
            return None
        try:
            _lock_file(probe)
        except OSError:
            # 加锁失败 = 确有进程持有，读出 PID 用于提示。
            return self._read_owner()
        else:
            _unlock_file(probe)
            return None
        finally:
            os.close(probe)


def default_lock_directory() -> Path:
    """锁文件所在目录。"""

    base = os.environ.get("TEMP") or os.environ.get("TMP") or "."
    return Path(base) / _LOCK_DIRECTORY_NAME


def _lock_file(handle: int) -> None:
    """对锁文件加非阻塞独占锁。

    使用 ``msvcrt.locking``：进程退出（含被强杀）时由操作系统释放，
    这是「崩溃后不残留死锁」的关键。非 Windows 平台退回 ``fcntl``。
    """

    try:
        import msvcrt

        msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
        return
    except ImportError:
        pass
    import fcntl

    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: int) -> None:
    """解除 :func:`_lock_file` 加的锁。"""

    try:
        import msvcrt

        msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
        return
    except ImportError:
        pass
    import fcntl

    fcntl.flock(handle, fcntl.LOCK_UN)
