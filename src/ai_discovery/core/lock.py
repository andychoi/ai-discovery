"""Single-flight run lock (roadmap item M1.4, ADR-0002).

One writer per project at a time. The lock file is created with ``O_EXCL`` and
holds ``{pid, hostname, started_at, token, purpose, proc_start}``.

Reclaim rules — a lock is taken over only when its owner is provably gone:
  * same host and the pid is no longer running, or
  * same host, the pid is running but started at a different time (pid reused).
A live owner is **never** evicted because the lock is old; a lock from another
host is never evicted automatically (``doctor --repair`` can, M2.5).
Release deletes the file only if it still holds our token.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SUSPECT_AGE_S = 30 * 60  # reported (not acted on) when a live lock is older
CORRUPT_GRACE_S = 10     # an unreadable lock younger than this may still be mid-write


class LockHeld(RuntimeError):
    """Another live process holds the lock. ``reason`` is the CLI reason slug."""

    reason = "lock_held"

    def __init__(self, path: Path, holder: dict):
        self.path = path
        self.holder = holder
        age = holder.get("_age_s")
        suspect = age is not None and age > SUSPECT_AGE_S
        super().__init__(
            f"Another discovery run holds {path} (pid {holder.get('pid')} on "
            f"{holder.get('hostname')}, {holder.get('purpose', 'run')} since {holder.get('started_at')})"
            + ("; it has been running a long time — check it is not stuck" if suspect else "")
        )


def _proc_start(pid: int) -> str | None:
    """Process start time as reported by ``ps`` (None if unknown)."""
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = out.stdout.strip()
    return value or None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


class RunLock:
    def __init__(self, path: Path | str, *, purpose: str = "scan"):
        self.path = Path(path)
        self.purpose = purpose
        self.token = uuid.uuid4().hex
        self._held = False

    # ── public API ────────────────────────────────────────────────────────
    def acquire(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            if self._try_create():
                self._held = True
                return self
            holder = self._read()
            if holder is None:
                time.sleep(0.05)  # creator may be mid-write
                holder = self._read()
            if holder is None:
                # Unreadable: a crashed writer's partial file, or one still
                # being written. Only reclaim once it is clearly old.
                if self._mtime_age() < CORRUPT_GRACE_S:
                    raise LockHeld(self.path, {"purpose": "unknown (lock being written)"})
                self.path.unlink(missing_ok=True)
                continue
            if self._is_stale(holder):
                self._remove_if(holder)
                continue
            raise LockHeld(self.path, holder)
        raise LockHeld(self.path, self._read() or {})

    def release(self) -> None:
        if not self._held:
            return
        holder = self._read()
        if holder is not None and holder.get("token") == self.token:
            self.path.unlink(missing_ok=True)
        self._held = False

    def __enter__(self) -> "RunLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()

    # ── internals ─────────────────────────────────────────────────────────
    def _try_create(self) -> bool:
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        info = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "token": self.token,
            "purpose": self.purpose,
            "proc_start": _proc_start(os.getpid()),
        }
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(info, fh)
        return True

    def _read(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}  # vanished: treat as stale (nothing to remove)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            started = datetime.fromisoformat(data.get("started_at", ""))
            data["_age_s"] = (datetime.now(timezone.utc) - started).total_seconds()
        except (TypeError, ValueError):
            pass
        return data

    @staticmethod
    def _is_stale(holder: dict) -> bool:
        if not holder:
            return True
        if holder.get("hostname") != socket.gethostname():
            return False  # can't see the other host's processes
        pid = int(holder.get("pid") or 0)
        if not _pid_alive(pid):
            return True
        recorded = holder.get("proc_start")
        if recorded:
            current = _proc_start(pid)
            if current is not None and current != recorded:
                return True  # pid reused by an unrelated process
        return False

    def _mtime_age(self) -> float:
        try:
            return time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return float("inf")

    def _remove_if(self, holder: dict) -> None:
        """Remove the lock only if it is still the one we judged stale."""
        current = self._read()
        if current and holder and current.get("token") == holder.get("token"):
            self.path.unlink(missing_ok=True)
