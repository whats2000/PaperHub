"""QA I5: an idea's citation is narrowed to the chunks that carry its mechanism."""
from __future__ import annotations

from collections import Counter

import aiosqlite

from paperhub.ideation.narrow import WINDOW, best_window, narrow, tokens


async def _paper(conn: aiosqlite.Connection, sections: dict[str, list[str]]) -> tuple[int, dict[str, list[int]]]:
    cur = await conn.execute(
        "INSERT INTO paper_content (content_key, kind, sha256, title, source_path, "
        "source_dir_path, html_path) VALUES ('pdf:n', 'pdf_upload', ?, 'P', '', '', '')",
        ("0" * 64,),
    )
    assert cur.lastrowid is not None
    pid = int(cur.lastrowid)
    ids: dict[str, list[int]] = {}
    pos = 0
    for name, texts in sections.items():
        for t in texts:
            c = await conn.execute(
                "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
                "VALUES (?, ?, ?, ?, ?)", (pid, name, pos, pos + len(t), t))
            assert c.lastrowid is not None
            ids.setdefault(name, []).append(int(c.lastrowid))
            pos += len(t) + 1
    await conn.commit()
    return pid, ids


_FILLER = [
    "Reference genome sequences were downloaded and aligned with standard tooling.",
    "Training data tracks were acquired from public consortia and normalised per sample.",
    "We held out chromosomes for validation and testing to avoid leakage.",
    "Batch size and learning rate followed a cosine schedule during pretraining.",
    "Evaluation metrics include Pearson correlation on held-out tracks.",
    "Hardware: models were trained on accelerator pods for several days.",
]
_UNET = [
    "The encoder downsamples the sequence through seven downres blocks, halving resolution each stage.",
    "Upsampling blocks restore resolution and add skip connections from the matching encoder stage.",
]


def test_tokens_drop_stop_words_and_fold_plurals() -> None:
    assert tokens("The skip connections of the U-Net") == ["skip", "connection", "u-net"]


def test_best_window_finds_the_scoring_run() -> None:
    chunks = [tokens(t) for t in [*_FILLER[:3], *_UNET, *_FILLER[3:]]]
    df: Counter[str] = Counter()
    for c in chunks:
        df.update(set(c))
    hit = best_window(tokens("downsample with skip connections and upsampling"),
                      chunks, df, len(chunks), sum(map(len, chunks)) / len(chunks))
    assert hit is not None
    start, end, score, terms = hit
    assert 3 <= start <= 4 < end <= 5 and score > 0 and terms >= 2


async def test_large_section_is_narrowed_to_the_mechanism_chunks(
    migrated_db: aiosqlite.Connection,
) -> None:
    body = [*_FILLER, *_UNET, *_FILLER]
    pid, ids = await _paper(migrated_db, {"Methods": body, "Results": ["Results text."]})
    got = await narrow(migrated_db, paper_content_id=pid, sections=["Methods"],
                       query_text="Downsample long context with skip connections; upsample")
    assert got is not None and got.narrowed and got.section == "Methods"
    assert got.chunk_ids == ids["Methods"][6:8]
    assert len(got.chunk_ids) <= WINDOW


async def test_nothing_scores_keeps_the_whole_section(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid, ids = await _paper(migrated_db, {"Methods": _FILLER})
    got = await narrow(migrated_db, paper_content_id=pid, sections=["Methods"],
                       query_text="quantum chromodynamics lattice")
    assert got is not None and not got.narrowed
    assert got.chunk_ids == ids["Methods"]


async def test_a_copied_cites_list_stores_the_section_holding_the_best_window(
    migrated_db: aiosqlite.Connection,
) -> None:
    pid, ids = await _paper(migrated_db, {"Intro": _FILLER[:2], "Architecture": _UNET})
    got = await narrow(migrated_db, paper_content_id=pid, sections=["Intro", "Architecture"],
                       query_text="skip connections from downres blocks")
    assert got is not None and got.section == "Architecture"
    assert got.chunk_ids == ids["Architecture"]


async def test_no_chunks_anywhere_is_none(migrated_db: aiosqlite.Connection) -> None:
    pid, _ids = await _paper(migrated_db, {"Methods": ["x y z"]})
    assert await narrow(migrated_db, paper_content_id=pid, sections=["Nope"],
                        query_text="x") is None
