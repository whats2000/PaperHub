"""``paperhub-copy-workspace`` — clone a workspace safely.

Used to back up a live workspace and to make the isolated copy the H4 instance
runs on. The SQLite file is copied with the online backup API (never a plain
file copy: a live DB has uncheckpointed ``-wal`` pages), ``papers_cache`` and
``exports`` are copied as trees, ``logs`` is skipped, and the absolute paths the
DB stores (``paper_content.source_path/source_dir_path/html_path``,
``decks.tex_path/pdf_path``) are rewritten from the source to the destination
workspace. The source is only ever read.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

DB_NAME = "paperhub.db"
_TREES = ("papers_cache", "exports")
_PATH_COLUMNS = (
    ("paper_content", "source_path"),
    ("paper_content", "source_dir_path"),
    ("paper_content", "html_path"),
    ("decks", "tex_path"),
    ("decks", "pdf_path"),
)


@dataclass(frozen=True)
class CopyReport:
    db_rows_rewritten: int
    files_copied: int


def _spellings(path: Path) -> list[str]:
    """Backslash and forward-slash spellings of ``path`` (deduplicated)."""
    s = str(path)
    out = [s.replace("/", "\\"), s.replace("\\", "/")]
    return list(dict.fromkeys(out))


def _rewrite_paths(db: Path, src: Path, dest: Path) -> int:
    pairs: list[tuple[str, str]] = []
    for sep_src, sep_dest in zip(_spellings(src), _spellings(dest), strict=True):
        if sep_src != sep_dest:
            pairs.append((sep_src, sep_dest))
    conn = sqlite3.connect(db)
    try:
        existing = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        changed = 0
        for table, col in _PATH_COLUMNS:
            if table not in existing:
                continue
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                continue
            for old, new in pairs:
                cur = conn.execute(
                    f"UPDATE {table} SET {col} = ? || substr({col}, ?) "
                    f"WHERE {col} IS NOT NULL AND lower(substr({col}, 1, ?)) = lower(?)",
                    (new, len(old) + 1, len(old), old),
                )
                changed += cur.rowcount
        conn.commit()
        return changed
    finally:
        conn.close()


def copy_workspace(src: Path, dest: Path, *, force: bool = False) -> CopyReport:
    src = Path(src).resolve()
    dest = Path(dest).resolve()
    if not (src / DB_NAME).is_file():
        raise FileNotFoundError(f"no {DB_NAME} in source workspace {src}")
    if src == dest:
        raise ValueError("source and destination workspaces are the same")
    if dest.exists() and not force:
        raise FileExistsError(f"destination exists: {dest} (use --force)")
    dest.mkdir(parents=True, exist_ok=True)
    for name in _TREES:
        shutil.rmtree(dest / name, ignore_errors=True)

    for suffix in ("", "-wal", "-shm"):
        (dest / f"{DB_NAME}{suffix}").unlink(missing_ok=True)
    # Read-only on the source: backup() reads pages, it never writes the source.
    src_conn = sqlite3.connect(f"file:{(src / DB_NAME).as_posix()}?mode=ro", uri=True)
    dest_conn = sqlite3.connect(dest / DB_NAME)
    try:
        src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()

    files = 1
    for name in _TREES:
        tree = src / name
        if not tree.is_dir():
            continue
        shutil.rmtree(dest / name, ignore_errors=True)  # --force: mirror, no stale files
        shutil.copytree(tree, dest / name)
        files += sum(1 for p in (dest / name).rglob("*") if p.is_file())

    return CopyReport(_rewrite_paths(dest / DB_NAME, src, dest), files)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Copy a PaperHub workspace safely.")
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--force", action="store_true", help="overwrite an existing dest")
    args = ap.parse_args(argv)
    try:
        report = copy_workspace(args.src, args.dest, force=args.force)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"copied {args.src} -> {args.dest}: {report.files_copied} files, "
        f"{report.db_rows_rewritten} path columns rewritten"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
