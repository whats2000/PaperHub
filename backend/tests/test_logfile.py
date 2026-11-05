"""Persistent backend log files (workspace/logs/): every record is flushed
immediately (readable while the server runs), uvicorn's access + error logs are
captured too, one file per start.ps1 run (findable by start time), and
retention-by-age + a total-size cap keep the directory bounded."""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from paperhub.logfile import (
    LOG_FILENAME,
    cleanup_logs,
    install_file_logging,
    log_filename,
)

_TARGETS = ("", "uvicorn", "uvicorn.access")


@pytest.fixture
def cleanup_handlers() -> Iterator[None]:
    before = {name: list(logging.getLogger(name).handlers) for name in _TARGETS}
    levels = {name: logging.getLogger(name).level for name in _TARGETS}
    # uvicorn sets its loggers to INFO at runtime; mirror that here.
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    yield
    for name in _TARGETS:
        lg = logging.getLogger(name)
        lg.setLevel(levels[name])
        for h in list(lg.handlers):
            if h not in before[name]:
                lg.removeHandler(h)
                h.close()


def _read(log_dir: Path) -> str:
    return (log_dir / LOG_FILENAME).read_text(encoding="utf-8")


def test_records_are_written_immediately(
    tmp_path: Path, cleanup_handlers: None,
) -> None:
    install_file_logging(tmp_path)
    logging.getLogger("paperhub.test").warning("backfill pending=%d", 2)
    # Readable right away — no explicit flush/close needed.
    text = _read(tmp_path)
    assert "WARNING" in text
    assert "paperhub.test" in text
    assert "backfill pending=2" in text


def test_captures_uvicorn_error_with_traceback_and_access(
    tmp_path: Path, cleanup_handlers: None,
) -> None:
    install_file_logging(tmp_path)
    try:
        raise RuntimeError("HTTP 429")
    except RuntimeError:
        logging.getLogger("uvicorn.error").exception("Exception in ASGI application")
    logging.getLogger("uvicorn.access").info('"POST /papers HTTP/1.1" 500')
    text = _read(tmp_path)
    assert "Exception in ASGI application" in text
    assert "RuntimeError: HTTP 429" in text
    assert "POST /papers" in text


def test_install_is_idempotent(tmp_path: Path, cleanup_handlers: None) -> None:
    install_file_logging(tmp_path)
    install_file_logging(tmp_path)
    logging.getLogger("paperhub.test").warning("once")
    assert _read(tmp_path).count("once") == 1


def test_rotation_bounds_disk_use(tmp_path: Path, cleanup_handlers: None) -> None:
    install_file_logging(tmp_path, max_bytes=2_000, backup_count=2)
    lg = logging.getLogger("paperhub.test")
    for i in range(200):
        lg.warning("line %d %s", i, "x" * 50)
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == [LOG_FILENAME, f"{LOG_FILENAME}.1", f"{LOG_FILENAME}.2"]
    handlers = [
        h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)
    ]
    assert len(handlers) == 1



def _detach() -> None:
    for name in _TARGETS:
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            if isinstance(h, RotatingFileHandler):
                lg.removeHandler(h)
                h.close()


def test_one_file_per_dev_run_and_reload_appends(
    tmp_path: Path, cleanup_handlers: None,
) -> None:
    lg = logging.getLogger("paperhub.test")
    p1 = install_file_logging(tmp_path, session="20261105-131100")
    assert p1.name == "paperhub-20261105-131100.log"
    lg.warning("first run")
    _detach()
    # --reload worker restart: same session id -> same file, appended.
    assert install_file_logging(tmp_path, session="20261105-131100") == p1
    lg.warning("after reload")
    _detach()
    p2 = install_file_logging(tmp_path, session="20261105-150000")
    lg.warning("second run")
    # The earlier run's log is still there, intact.
    assert p1.read_text(encoding="utf-8").count("run") == 1
    assert "after reload" in p1.read_text(encoding="utf-8")
    assert "second run" in p2.read_text(encoding="utf-8")


def test_log_filename_sanitises_session() -> None:
    assert log_filename(None) == LOG_FILENAME
    assert log_filename("") == LOG_FILENAME
    assert log_filename("../x y") == "paperhub-___x_y.log"


def _make(path: Path, size: int, age_days: float) -> Path:
    path.write_bytes(b"x" * size)
    t = time.time() - age_days * 86400
    os.utime(path, (t, t))
    return path


def test_cleanup_deletes_logs_past_retention(tmp_path: Path) -> None:
    old = _make(tmp_path / "paperhub-old.log", 10, age_days=20)
    old_backup = _make(tmp_path / "paperhub-old.log.1", 10, age_days=20)
    recent = _make(tmp_path / "paperhub-recent.log", 10, age_days=3)
    other = _make(tmp_path / "notes.txt", 10, age_days=99)
    deleted = cleanup_logs(tmp_path, retention_days=14, max_total_bytes=10**9)
    assert set(deleted) == {old, old_backup}
    assert recent.exists()
    assert other.exists()  # only paperhub log files are ever touched


def test_cleanup_enforces_total_size_oldest_first(tmp_path: Path) -> None:
    a = _make(tmp_path / "paperhub-a.log", 400, age_days=3)
    b = _make(tmp_path / "paperhub-b.log", 400, age_days=2)
    c = _make(tmp_path / "paperhub-c.log", 400, age_days=1)
    deleted = cleanup_logs(tmp_path, retention_days=14, max_total_bytes=900)
    assert deleted == [a]
    assert b.exists() and c.exists()


def test_cleanup_never_deletes_the_active_run(tmp_path: Path) -> None:
    active = _make(tmp_path / "paperhub-now.log", 1000, age_days=30)
    active_backup = _make(tmp_path / "paperhub-now.log.1", 1000, age_days=30)
    older = _make(tmp_path / "paperhub-prev.log", 100, age_days=1)
    deleted = cleanup_logs(
        tmp_path, keep=active, retention_days=14, max_total_bytes=500,
    )
    assert deleted == [older]
    assert active.exists() and active_backup.exists()


def test_many_runs_stay_under_the_total_cap(
    tmp_path: Path, cleanup_handlers: None,
) -> None:
    lg = logging.getLogger("paperhub.test")
    for run in range(30):
        install_file_logging(
            tmp_path, session=f"run{run:02d}", max_total_bytes=2_000,
        )
        lg.warning("run %d %s", run, "x" * 300)
        _detach()
        # Distinct mtimes so "oldest" is well-defined.
        for p in tmp_path.glob("paperhub-run*.log"):
            n = int(p.stem.removeprefix("paperhub-run"))
            os.utime(p, (time.time() - (100 - n), time.time() - (100 - n)))
    total = sum(p.stat().st_size for p in tmp_path.glob("paperhub*.log*"))
    # Cap applies at each start, so at most one run's worth over the cap.
    assert total <= 2_000 + 600
    assert (tmp_path / "paperhub-run29.log").exists()
    assert not (tmp_path / "paperhub-run00.log").exists()
