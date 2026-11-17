from __future__ import annotations

import fcntl
import os
from pathlib import Path
from types import TracebackType
from typing import Literal


class ExclusiveProcessLock:
    """Kernel-managed exclusive lock for one Paper database and process role."""

    def __init__(self, database: str | Path, role: Literal["supervisor", "worker"]) -> None:
        raw = str(database)
        if raw == ":memory:":
            raise RuntimeError("Exclusive Paper process locking requires a file-backed database.")
        database_path = Path(raw).expanduser().resolve()
        self.path = database_path.with_name(database_path.name + f".{role}.lock")
        self._fd: int | None = None

    def acquire(self) -> "ExclusiveProcessLock":
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        fd = os.open(self.path, flags, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise RuntimeError(
                f"Another Paper {self.path.name.rsplit('.', 2)[-2]} process already owns "
                f"this database lock: {self.path}"
            ) from exc
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> "ExclusiveProcessLock":
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def paper_process_lock(
    database: str | Path, role: Literal["supervisor", "worker"]
) -> ExclusiveProcessLock:
    return ExclusiveProcessLock(database, role)
