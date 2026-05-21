"""Tests for paperhub.pipelines.sentinels — TDD: written before implementation."""
from __future__ import annotations

import aiosqlite
import pytest

from paperhub.pipelines.sentinels import (
    find_math_spans,
    inject_sentinels,
    postprocess_sentinels,
    sentinel_token,
)

# ---------------------------------------------------------------------------
# sentinel_token
# ---------------------------------------------------------------------------

def test_sentinel_token_format() -> None:
    assert sentinel_token(5) == "PHCHUNKANCHOR5END"
    assert sentinel_token(0) == "PHCHUNKANCHOR0END"
    assert sentinel_token(99) == "PHCHUNKANCHOR99END"


# ---------------------------------------------------------------------------
# find_math_spans
# ---------------------------------------------------------------------------

def test_find_math_spans_inline_dollar() -> None:
    text = "before $x$ after"
    spans = find_math_spans(text)
    assert len(spans) == 1
    s, e = spans[0]
    assert text[s:e] == "$x$"


def test_find_math_spans_display_brackets() -> None:
    text = r"before \[y\] after"
    spans = find_math_spans(text)
    assert len(spans) == 1
    s, e = spans[0]
    assert text[s:e] == r"\[y\]"


def test_find_math_spans_align_env() -> None:
    text = r"text \begin{align} a &= b \end{align} more"
    spans = find_math_spans(text)
    assert len(spans) == 1
    s, e = spans[0]
    assert r"\begin{align}" in text[s:e]
    assert r"\end{align}" in text[s:e]


def test_find_math_spans_multiple() -> None:
    text = r"$a$ and \[b\] here"
    spans = find_math_spans(text)
    assert len(spans) == 2


def test_find_math_spans_none() -> None:
    text = "plain text with no math"
    spans = find_math_spans(text)
    assert spans == []


# ---------------------------------------------------------------------------
# inject_sentinels
# ---------------------------------------------------------------------------

def test_inject_sentinels_single() -> None:
    base = "Hello world"
    marked, injected = inject_sentinels(base, [6])
    assert 0 in injected
    assert sentinel_token(0) in marked
    # Text before the start position is unchanged.
    assert marked.startswith("Hello ")
    # Token is followed by "world".
    tok = sentinel_token(0)
    idx = marked.index(tok)
    assert marked[idx + len(tok):] == "world"


def test_inject_sentinels_at_position_zero() -> None:
    base = "start of text"
    marked, injected = inject_sentinels(base, [0])
    assert 0 in injected
    assert marked.startswith(sentinel_token(0))


def test_inject_sentinels_multiple_back_to_front_preserves_offsets() -> None:
    """With two starts, both tokens must be present and placed correctly.

    Back-to-front insertion means earlier offsets aren't shifted by later
    insertions — we verify by finding each token in the final string and
    checking that the original surrounding characters are correct.
    """
    base = "ABCDEFGHIJ"
    starts = [2, 7]  # ordinal 0 → pos 2 ('C'), ordinal 1 → pos 7 ('H')
    marked, injected = inject_sentinels(base, starts)

    assert injected == {0, 1}
    tok0 = sentinel_token(0)
    tok1 = sentinel_token(1)
    assert tok0 in marked
    assert tok1 in marked

    # Characters surrounding each original position must be intact.
    # Original: A B C D E F G H I J
    #           0 1 2 3 4 5 6 7 8 9
    # ordinal 0 inserted at 2 → "AB<tok0>CDEFGHIJ"
    # ordinal 1 inserted at 7 → offset 7 in original text → 'H'
    # After back-to-front: ordinal 1 first (pos 7 → "ABCDEFG<tok1>HIJ"),
    # then ordinal 0 (pos 2 → "AB<tok0>CDEFG<tok1>HIJ").

    # Verify: right before tok0 is "AB", right after tok0 is "CDEFG"+tok1+"HIJ".
    idx0 = marked.index(tok0)
    assert marked[:idx0] == "AB"
    # Everything after tok0 starts with "CDEFG".
    after_tok0 = marked[idx0 + len(tok0):]
    assert after_tok0.startswith("CDEFG")

    # tok1 comes after tok0's region.
    idx1 = marked.index(tok1)
    assert idx1 > idx0
    # After tok1, "HIJ" follows.
    assert marked[idx1 + len(tok1):] == "HIJ"


def test_inject_sentinels_skips_inside_inline_math() -> None:
    base = r"text $x + y$ more"
    # Position 6 is inside "$x + y$" (starts at 5, ends at 12).
    dollar_start = base.index("$")
    inside = dollar_start + 2  # definitely inside
    marked, injected = inject_sentinels(base, [inside])
    assert 0 not in injected
    assert sentinel_token(0) not in marked


def test_inject_sentinels_skips_inside_equation_env() -> None:
    base = r"before \begin{equation} E=mc^2 \end{equation} after"
    inside = base.index("E")  # inside the equation env
    marked, injected = inject_sentinels(base, [inside])
    assert 0 not in injected
    assert sentinel_token(0) not in marked


def test_inject_sentinels_does_not_skip_outside_math() -> None:
    base = r"before $x$ AFTER"
    outside = base.index("A")  # outside math
    marked, injected = inject_sentinels(base, [outside])
    assert 0 in injected
    assert sentinel_token(0) in marked


# ---------------------------------------------------------------------------
# postprocess_sentinels
# ---------------------------------------------------------------------------

def test_postprocess_sentinels_basic() -> None:
    html = f"<p>{sentinel_token(3)}some text</p>"
    new_html, found = postprocess_sentinels(html)
    assert '<span id="phchunk-3"></span>' in new_html
    assert found == {3: "phchunk-3"}
    # Original token gone.
    assert sentinel_token(3) not in new_html


def test_postprocess_sentinels_multiple() -> None:
    html = f"{sentinel_token(0)}first{sentinel_token(1)}second"
    new_html, found = postprocess_sentinels(html)
    assert found == {0: "phchunk-0", 1: "phchunk-1"}
    assert '<span id="phchunk-0"></span>' in new_html
    assert '<span id="phchunk-1"></span>' in new_html


def test_postprocess_sentinels_missing_token_not_in_dict() -> None:
    """A token that doesn't appear in the html → not in the returned dict."""
    html = "<p>no sentinel here</p>"
    new_html, found = postprocess_sentinels(html)
    assert found == {}
    assert new_html == html


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

def test_round_trip_inject_then_postprocess() -> None:
    """inject_sentinels → postprocess_sentinels yields <span id="phchunk-i">
    for each injected ordinal; the dict maps i → 'phchunk-i'."""
    base = "Introduction text. More content here. Final words."
    starts = [0, 20, 38]
    marked, injected = inject_sentinels(base, starts)
    new_html, found = postprocess_sentinels(marked)

    for i in injected:
        dom_id = f"phchunk-{i}"
        assert dom_id in found.values()
        assert f'<span id="{dom_id}"></span>' in new_html

    # Non-injected ordinals are absent from found.
    not_injected = set(range(len(starts))) - injected
    for i in not_injected:
        assert i not in found


# ---------------------------------------------------------------------------
# Schema: chunks.dom_id column exists after migration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chunks_has_dom_id_column(migrated_db: aiosqlite.Connection) -> None:
    async with migrated_db.execute("PRAGMA table_info(chunks)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    assert "dom_id" in cols


@pytest.mark.asyncio
async def test_chunks_dom_id_is_nullable(migrated_db: aiosqlite.Connection) -> None:
    """dom_id must be nullable — existing chunks have no anchor yet."""
    # Insert a parent paper_content row first.
    await migrated_db.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:test', 'arxiv', '0000.00001', 'Test Paper', '/s', '/d', '/h')"
    )
    await migrated_db.commit()
    await migrated_db.execute(
        "INSERT INTO chunks (paper_content_id, char_start, char_end, text) "
        "VALUES (1, 0, 10, 'hello')"
    )
    await migrated_db.commit()
    async with migrated_db.execute("SELECT dom_id FROM chunks WHERE id = 1") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] is None  # nullable, no value set


@pytest.mark.asyncio
async def test_chunks_dom_id_accepts_value(migrated_db: aiosqlite.Connection) -> None:
    """dom_id can be set to a string anchor like 'phchunk-7'."""
    await migrated_db.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:test2', 'arxiv', '0000.00002', 'Test2', '/s', '/d', '/h')"
    )
    await migrated_db.commit()
    await migrated_db.execute(
        "INSERT INTO chunks (paper_content_id, char_start, char_end, text, dom_id) "
        "VALUES (1, 0, 10, 'hello', 'phchunk-7')"
    )
    await migrated_db.commit()
    async with migrated_db.execute("SELECT dom_id FROM chunks WHERE dom_id = 'phchunk-7'") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "phchunk-7"
