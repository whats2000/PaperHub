"""Persistent backend log files — ``<workspace>/logs/``.

The console log is only visible in the operator's terminal; this mirrors it to
a file so a debugging session can read what the server actually logged
(external-API failures, ASGI tracebacks, background-loop decisions) without
copy-pasting from the terminal.

Findable when needed:

* One file per dev run — ``paperhub-<session>.log``, where ``session`` is the
  start timestamp ``start.ps1`` passes via ``PAPERHUB_LOG_SESSION`` (e.g.
  ``paperhub-20261105-131100.log``). A ``--reload`` worker restart inherits the
  same id and keeps appending to the same file. Bare uvicorn (no session) uses
  ``paperhub.log``.
* Every record is flushed as it is written and carries a timestamp, so it can be
  matched against ``runs`` rows in the DB.
* Captures the root logger (all ``paperhub.*`` + library loggers) AND
  uvicorn's ``uvicorn`` / ``uvicorn.access`` loggers, which uvicorn configures
  with ``propagate=False`` — ASGI tracebacks and the access log included.

Never grows without bound:

* On install, log files older than ``retention_days`` are deleted, then the
  oldest are deleted until the directory's total is under ``max_total_bytes``.
* Within one run, size rotation caps a single file at ``max_bytes`` ×
  (``backup_count`` + 1).

Disabled with ``PAPERHUB_LOG_FILE=0`` (tests).
"""
from __future__ import annotations

import logging
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILENAME = "paperhub.log"
DEFAULT_RETENTION_DAYS = 14
DEFAULT_MAX_TOTAL_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 2
_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
# Loggers uvicorn sets to propagate=False (plus root for everything else).
_TARGET_LOGGERS = ("", "uvicorn", "uvicorn.access")
_UNSAFE_SESSION_CHARS = re.compile(r"[^A-Za-z0-9_-]")


class _PaperhubFileHandler(RotatingFileHandler):
    """Rotating file handler shared by several loggers.

    When a logger it is attached to propagates to another one it is also
    attached to (e.g. ``uvicorn`` → root outside uvicorn's own config), the same
    record would be written twice — skip a record identical to the last one.
    """

    _last: logging.LogRecord | None = None

    def emit(self, record: logging.LogRecord) -> None:
        if record is self._last:
            return
        self._last = record
        super().emit(record)


def log_filename(session: str | None) -> str:
    """``paperhub-<session>.log`` for a start.ps1 run, else ``paperhub.log``."""
    if not session:
        return LOG_FILENAME
    safe = _UNSAFE_SESSION_CHARS.sub("_", session)[:64]
    return f"paperhub-{safe}.log"


def cleanup_logs(
    log_dir: Path,
    *,
    keep: Path | None = None,
    retention_days: float = DEFAULT_RETENTION_DAYS,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> list[Path]:
    """Delete log files past retention, then oldest-first until the total is
    under ``max_total_bytes``. ``keep`` (the active run's file + its rotated
    backups) is never deleted. Returns the deleted paths."""
    files = [
        p for p in log_dir.glob("paperhub*.log*")
        if p.is_file() and not (keep is not None and p.name.startswith(keep.name))
    ]
    files.sort(key=lambda p: p.stat().st_mtime)
    deleted: list[Path] = []
    cutoff = time.time() - retention_days * 86400
    for p in list(files):
        if p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
            deleted.append(p)
            files.remove(p)
    kept_size = 0
    if keep is not None:
        kept_size = sum(
            p.stat().st_size for p in log_dir.glob(f"{keep.name}*") if p.is_file()
        )
    total = kept_size + sum(p.stat().st_size for p in files)
    for p in files:
        if total <= max_total_bytes:
            break
        total -= p.stat().st_size
        p.unlink(missing_ok=True)
        deleted.append(p)
    return deleted


def install_file_logging(
    log_dir: Path,
    *,
    session: str | None = None,
    retention_days: float = DEFAULT_RETENTION_DAYS,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> Path:
    """Clean up old logs, then attach the file handler (idempotent).
    Returns the active log path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / log_filename(session)
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, _PaperhubFileHandler):
            return Path(h.baseFilename)
    cleanup_logs(
        log_dir, keep=path,
        retention_days=retention_days, max_total_bytes=max_total_bytes,
    )
    handler = _PaperhubFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_FORMAT))
    for name in _TARGET_LOGGERS:
        logging.getLogger(name).addHandler(handler)
    return path
