"""One running copy per job (collector, trader), enforced with an OS file lock held for the life of the process.

The operating system drops the lock the moment the process exits or crashes, so a stale lock can never block a
restart, and nothing else on the machine (an editor, a terminal tailing the log, a command that merely mentions
`main.py collect`) can be mistaken for a running copy — which is what the old `pgrep -f` check did.
"""
from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from core.config import PROJECT_ROOT

LOCK_DIR = PROJECT_ROOT / "data" / "storage" / "locks"
JOBS = ("collector", "trader")


class AlreadyRunning(RuntimeError):
    pass


def _path(job: str, lock_dir: Path | None) -> Path:
    if job not in JOBS:
        raise ValueError(f"unknown job {job!r}; expected one of {JOBS}")
    d = lock_dir or LOCK_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{job}.lock"


@contextmanager
def single_instance(job: str, wait: float = 2.0, lock_dir: Path | None = None) -> Iterator[None]:
    """Hold the job's lock while the block runs; AlreadyRunning if another process holds it after `wait` seconds."""
    f = open(_path(job, lock_dir), "a+")
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                f.close()
                raise AlreadyRunning(f"{job} is already running") from None
            time.sleep(0.1)
    try:
        f.seek(0)
        f.truncate()
        f.write(f"{os.getpid()}\n")                  # for people reading the file; the lock itself is what counts
        f.flush()
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def is_running(job: str, lock_dir: Path | None = None) -> bool:
    """True while some process holds the job's lock."""
    p = _path(job, lock_dir)
    if not p.exists():
        return False
    with open(p) as f:
        try:
            fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
        return False
