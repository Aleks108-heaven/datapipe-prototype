"""Append-only, hash-chained audit log (tamper-evident, not tamper-proof).

Records hold identifiers, counts and hashes only - never raw data values.
Note: deleting the *tail* of the log is not detectable from the file alone; anchor the printed head hash elsewhere.
"""
import contextlib
import getpass
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .errors import AuditError

try:
    import fcntl
except ImportError:                     # Windows
    fcntl = None
    import msvcrt

GENESIS = "0" * 64


@contextlib.contextmanager
def _exclusive(fh, path):
    """Exclusive lock held across threads and processes: flock on the log itself (POSIX), or a byte-range lock on a
    separate `<log>.lock` file (Windows). Windows byte-range locks are mandatory, so locking the log itself would make
    every concurrent reader of the log fail with PermissionError; nobody ever reads the lock file."""
    if fcntl is not None:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
        return
    with open(str(path) + ".lock", "a+b") as lock:
        fd = lock.fileno()
        while True:                      # LK_NBLCK + sleep: LK_LOCK gives up after ~10 s
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(0.02)
        try:
            yield
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(record):
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(_canon(body).encode()).hexdigest()


def default_actor():
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


class AuditLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event, run_id=None, data=None, actor=None, guard=None):
        """Append one record. `guard(records)` runs while the file lock is held (raise to refuse), which makes
        check-then-append decisions atomic across threads and processes."""
        with open(self.path, "a+", encoding="utf-8") as fh:
            with _exclusive(fh, self.path):
                fh.seek(0)
                last = None
                lines = []
                for line in fh:
                    if line.strip():
                        last = line
                        if guard is not None:
                            lines.append(line)
                if guard is not None:
                    guard([json.loads(x) for x in lines])
                prev, seq = GENESIS, 0
                if last:
                    prev_rec = json.loads(last)
                    prev, seq = prev_rec["hash"], prev_rec["seq"] + 1
                record = {
                    "seq": seq,
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "event": event,
                    "run_id": run_id,
                    "actor": actor or default_actor(),
                    "data": data or {},
                    "prev": prev,
                }
                record["hash"] = _digest(record)
                fh.write(_canon(record) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                return record

    def records(self):
        if not self.path.exists():
            return []
        out = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    out.append(json.loads(line))
        return out

    def verify(self):
        """Return (ok, record_count, message)."""
        prev, expected_seq, n = GENESIS, 0, 0
        try:
            records = self.records()
        except ValueError:
            return False, 0, "log contains a line that is not valid JSON"
        for rec in records:
            n += 1
            if rec.get("seq") != expected_seq:
                return False, n, f"sequence gap or reorder at record {n}"
            if rec.get("prev") != prev:
                return False, n, f"broken chain at record {n}"
            if rec.get("hash") != _digest(rec):
                return False, n, f"record {n} was modified"
            prev, expected_seq = rec["hash"], expected_seq + 1
        return True, n, f"chain intact; head={prev}"

    def head(self):
        recs = self.records()
        return recs[-1]["hash"] if recs else GENESIS


_ = AuditError
