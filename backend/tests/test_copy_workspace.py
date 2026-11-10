"""copy_workspace: safe workspace clone for the isolated H4 instance."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from paperhub.cli.copy_workspace import copy_workspace


def _make_src(tmp_path: Path) -> Path:
    src = tmp_path / "src ws"
    (src / "papers_cache" / "k1").mkdir(parents=True)
    (src / "papers_cache" / "k1" / "source.html").write_text("hi", encoding="utf-8")
    (src / "exports").mkdir()
    (src / "exports" / "e.txt").write_text("e", encoding="utf-8")
    (src / "logs").mkdir()
    (src / "logs" / "x.log").write_text("log", encoding="utf-8")
    return src


def _init_db(src: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(src / "paperhub.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.executescript(
        """
        CREATE TABLE paper_content(id INTEGER PRIMARY KEY, source_path TEXT,
            source_dir_path TEXT, html_path TEXT);
        CREATE TABLE decks(id INTEGER PRIMARY KEY, tex_path TEXT, pdf_path TEXT);
        """
    )
    conn.commit()
    return conn


def test_copy_uses_backup_api_and_keeps_wal_rows(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    conn = _init_db(src)
    conn.execute("INSERT INTO paper_content(source_path) VALUES ('wal-row')")
    conn.commit()  # lives in the -wal file, not yet checkpointed
    dest = tmp_path / "dest"
    copy_workspace(src, dest)
    out = sqlite3.connect(dest / "paperhub.db")
    assert out.execute("SELECT source_path FROM paper_content").fetchall() == [("wal-row",)]
    assert (dest / "papers_cache" / "k1" / "source.html").read_text("utf-8") == "hi"
    assert (dest / "exports" / "e.txt").exists()
    assert not (dest / "logs").exists()
    out.close()
    conn.close()


def test_copy_rewrites_absolute_paths(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    dest = tmp_path / "dest"
    conn = _init_db(src)
    s = str(src)
    sb = s.replace("/", "\\")
    sf = s.replace("\\", "/")
    conn.execute(
        "INSERT INTO paper_content(source_path, source_dir_path, html_path) VALUES (?,?,?)",
        (sb + r"\papers_cache\k1\a.tex", sf + "/papers_cache/k1", "/elsewhere/x.html"),
    )
    conn.execute(
        "INSERT INTO decks(tex_path, pdf_path) VALUES (?, NULL)", (sf + "/d.tex",)
    )
    conn.commit()
    conn.close()
    report = copy_workspace(src, dest)
    out = sqlite3.connect(dest / "paperhub.db")
    row = out.execute(
        "SELECT source_path, source_dir_path, html_path FROM paper_content"
    ).fetchone()
    assert row[0].startswith(str(dest).replace("/", "\\"))
    assert row[1].startswith(str(dest).replace("\\", "/"))
    assert row[2] == "/elsewhere/x.html"
    assert out.execute("SELECT tex_path FROM decks").fetchone()[0].startswith(
        str(dest).replace("\\", "/")
    )
    assert report.db_rows_rewritten == 3
    out.close()


def test_copy_refuses_existing_dest_without_force(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    _init_db(src).close()
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(FileExistsError):
        copy_workspace(src, dest)
    copy_workspace(src, dest, force=True)
    assert (dest / "paperhub.db").exists()


def test_force_copy_removes_stale_files(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    _init_db(src).close()
    dest = tmp_path / "dest"
    (dest / "papers_cache").mkdir(parents=True)
    (dest / "papers_cache" / "stale.txt").write_text("x", encoding="utf-8")
    copy_workspace(src, dest, force=True)
    assert not (dest / "papers_cache" / "stale.txt").exists()
    assert (dest / "papers_cache" / "k1" / "source.html").exists()


def test_copy_rewrite_is_case_insensitive(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    conn = _init_db(src)
    lowered = str(src).lower().replace("/", "\\")
    conn.execute("INSERT INTO decks(tex_path) VALUES (?)", (lowered + r"\d.tex",))
    conn.commit()
    conn.close()
    dest = tmp_path / "dest"
    report = copy_workspace(src, dest)
    out = sqlite3.connect(dest / "paperhub.db")
    stored = out.execute("SELECT tex_path FROM decks").fetchone()[0]
    assert stored.startswith(str(dest).replace("/", "\\"))
    assert report.db_rows_rewritten == 1
    out.close()
