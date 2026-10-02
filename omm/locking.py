"""Small cross-process lock for writes to one shared OMM data folder."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import threading
from typing import Iterator


_local = threading.local()


def _acquire(stream) -> None:
    if os.name == "nt":
        import msvcrt
        stream.seek(0)
        if stream.read(1) == "":
            stream.write("\0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _release(stream) -> None:
    if os.name == "nt":
        import msvcrt
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def data_lock(root: Path) -> Iterator[None]:
    """Serialize OMM operations from threads/processes sharing the same folder.

    The lock file is ignored by Git synchronization and never contains memory.
    Nesting in one thread is supported so service operations can call each other.
    """
    resolved = root.resolve()
    locks = getattr(_local, "locks", None)
    if locks is None:
        locks = _local.locks = {}
    key = str(resolved)
    entry = locks.get(key)
    if entry:
        stream, depth = entry
        locks[key] = (stream, depth + 1)
        try:
            yield
        finally:
            stream, depth = locks[key]
            locks[key] = (stream, depth - 1)
        return

    git_metadata = resolved / ".git"
    # Keep coordination state outside the visible working tree once the data
    # folder is a repository. A fresh pre-restore folder still uses the root.
    lock_path = ((git_metadata / "omm-write.lock") if git_metadata.is_dir()
                 else resolved / ".omm-write.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as stream:
        _acquire(stream)
        locks[key] = (stream, 1)
        try:
            yield
        finally:
            locks.pop(key, None)
            _release(stream)
