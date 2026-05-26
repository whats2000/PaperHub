"""List cached papers (content_key + title) so you can build a config.

    uv run python -m benchmark.list_papers [--db workspace/paperhub.db]
"""
from __future__ import annotations

import argparse
import sqlite3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="workspace/paperhub.db")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        rows = conn.execute(
            "SELECT content_key, kind, title FROM paper_content ORDER BY kind, content_key"
        ).fetchall()
    finally:
        conn.close()
    for key, kind, title in rows:
        print(f"{key}\t[{kind}]\t{title}")
    print(f"\n{len(rows)} cached papers")


if __name__ == "__main__":
    main()
