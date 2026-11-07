"""Rename a paper's sections in place and carry the rename to every consumer.

A section name is stored on ``chunks.section`` and copied into
``paper_content.sections_json``, the cached ``<source_dir>/digest.json``, the
mechanism card (``mechanism_cards.card_json`` + its ``digest_hash``), ideas'
``source_section`` / ``target_section``, slide grounding
(``deck_slides.source_sections_json``) and ``% cite: <paper>:<section>``
markers in the deck TeX. Chunk rows are updated by id — ids, offsets, text,
``dom_id``, ``page`` and ``bbox`` never change — so message ``[chunk:N]``
citations and stored chunk-id lists keep resolving.

Every step is a no-op when the old name is already gone, so re-running is safe.
The pure ``rename_*`` helpers carry the logic; :func:`apply_section_renames`
does the I/O.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

from paperhub.pipelines.section_names import is_heading_shaped, is_wrapped_continuation

__all__ = [
    "RenameReport",
    "apply_section_renames",
    "rename_card_json",
    "rename_cite_lines",
    "rename_digest",
    "rename_sections_json",
    "rename_source_sections",
    "repair_stored_names",
]

_CITE_LINE_RE = re.compile(r"(?im)^([ \t]*%[ \t]*cite:[ \t]*)(.+?)([ \t]*)$")


# --- pure helpers ---------------------------------------------------------------


def rename_sections_json(raw: str | None, mapping: Mapping[str, str]) -> str | None:
    """Rename ``sections_json`` entries. Entries that end up sharing a name are
    merged like ``PaperPipeline._build_sections_json`` groups chunks: first
    appearance order, extent from the first start to the last end, counts
    summed."""
    if not raw:
        return raw
    entries = json.loads(raw)
    merged: dict[str, dict[str, Any]] = {}
    for e in entries:
        name = mapping.get(e["name"], e["name"])
        if name not in merged:
            merged[name] = {**e, "name": name}
            continue
        m = merged[name]
        m["char_start"] = min(m["char_start"], e["char_start"])
        m["char_end"] = max(m["char_end"], e["char_end"])
        m["token_count"] = m.get("token_count", 0) + e.get("token_count", 0)
        m["chunk_count"] = m.get("chunk_count", 0) + e.get("chunk_count", 0)
    return json.dumps(list(merged.values()))


def _rename_list(names: list[Any], mapping: Mapping[str, str]) -> list[Any]:
    out: list[Any] = []
    for n in names:
        new = mapping.get(n, n) if isinstance(n, str) else n
        if new not in out:
            out.append(new)
    return out


def rename_card_json(raw: str, mapping: Mapping[str, str]) -> str:
    """Rename every ``"sections": [...]`` list in a mechanism card (deduped)."""
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                k: _rename_list(v, mapping) if k == "sections" and isinstance(v, list)
                else walk(v)
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    card = json.loads(raw)
    renamed = walk(card)
    return raw if renamed == card else json.dumps(renamed, ensure_ascii=False)


def rename_digest(digest: dict[str, Any], mapping: Mapping[str, str]) -> dict[str, Any]:
    """Rename a ``PaperDigest`` dict's sections; sections that now share a name
    become one entry whose insight joins theirs."""
    merged: dict[str, dict[str, Any]] = {}
    for s in digest.get("sections", []):
        name = mapping.get(s["name"], s["name"])
        if name in merged:
            prev = merged[name]["insight"]
            add = s.get("insight", "")
            merged[name]["insight"] = f"{prev} {add}".strip() if add else prev
        else:
            merged[name] = {**s, "name": name}
    return {**digest, "sections": list(merged.values())}


def rename_source_sections(
    raw: str, paper_id: int, mapping: Mapping[str, str],
) -> str:
    """Rename ``deck_slides.source_sections_json`` entries of ``paper_id``;
    entries that collide merge their ``chunk_ids``."""
    items = json.loads(raw or "[]")
    out: list[dict[str, Any]] = []
    for it in items:
        if it.get("paper_id") == paper_id:
            it = {**it, "section_name": mapping.get(it["section_name"], it["section_name"])}
        twin = next(
            (o for o in out if o.get("paper_id") == it.get("paper_id")
             and o.get("section_name") == it.get("section_name")),
            None,
        )
        if twin is None:
            out.append(dict(it))
        else:
            twin["chunk_ids"] = list(dict.fromkeys(
                [*twin.get("chunk_ids", []), *it.get("chunk_ids", [])]))
    return raw if out == items else json.dumps(out)


def rename_cite_lines(tex: str, paper_id: int, mapping: Mapping[str, str]) -> str:
    """Rename ``<paper_id>:<old>`` sources inside ``% cite:`` marker lines."""
    def fix(m: re.Match[str]) -> str:
        parts = []
        for part in m.group(2).split(";"):
            pid, sep, section = part.strip().partition(":")
            if sep and pid.strip() == str(paper_id) and section.strip() in mapping:
                part = f"{pid.strip()}:{mapping[section.strip()]}"
            parts.append(part.strip())
        # Two old names renamed to one section collapse to one source.
        return f"{m.group(1)}{'; '.join(dict.fromkeys(parts))}{m.group(3)}"

    return _CITE_LINE_RE.sub(fix, tex)


def repair_stored_names(names: list[str | None]) -> list[str | None]:
    """Section names for chunks (in document order) that cannot be re-derived
    from source — Marker block-anchored chunks. Mirrors the extraction rules:
    the lowercase tail of a wrapped heading joins the heading before it, and
    any other name that is not heading-shaped takes the enclosing real
    heading's name (kept as-is when no real heading precedes it)."""
    out = list(names)
    current: str | None = None
    head_start = -1  # index where the current heading's run starts
    prev_was_heading = False
    i = 0
    while i < len(out):
        name = out[i]
        j = i
        while j < len(out) and names[j] == names[i]:
            j += 1
        if name is None:
            prev_was_heading = False
        elif is_heading_shaped(name):
            current, head_start, prev_was_heading = name, i, True
        elif prev_was_heading and current is not None and is_wrapped_continuation(name):
            current = f"{current} {name}"
            for k in range(head_start, j):
                out[k] = current
        else:
            if current is not None:
                for k in range(i, j):
                    out[k] = current
            prev_was_heading = False
        i = j
    return out


# --- I/O ---------------------------------------------------------------------------


@dataclass
class RenameReport:
    chunks: int = 0
    sections_json: bool = False
    digest: bool = False
    card: bool = False
    card_hash_resynced: bool = False
    ideas: int = 0
    deck_slides: int = 0
    deck_tex_files: int = 0
    notes: list[str] = field(default_factory=list)


async def apply_section_renames(
    conn: aiosqlite.Connection,
    paper_content_id: int,
    chunk_names: Mapping[int, str | None],
) -> RenameReport:
    """Set ``chunks.section`` per chunk id and propagate the implied
    old→new name mapping to every consumer. Commits once at the end."""
    rep = RenameReport()
    async with conn.execute(
        "SELECT id, section FROM chunks WHERE paper_content_id = ?", (paper_content_id,),
    ) as cur:
        current = {int(r[0]): r[1] for r in await cur.fetchall()}
    mapping: dict[str, str] = {}
    for cid, new in chunk_names.items():
        old = current.get(cid)
        if cid not in current or old == new:
            continue
        if old is not None and new is not None and mapping.setdefault(old, new) != new:
            rep.notes.append(f"{old!r} maps to several names; consumers keep {mapping[old]!r}")
        await conn.execute("UPDATE chunks SET section = ? WHERE id = ?", (new, cid))
        rep.chunks += 1
    if not mapping:
        await conn.commit()
        return rep

    async with conn.execute(
        "SELECT sections_json, source_dir_path FROM paper_content WHERE id = ?",
        (paper_content_id,),
    ) as cur:
        row = await cur.fetchone()
    sections_raw, source_dir = (row[0], row[1]) if row else (None, None)
    new_sections = rename_sections_json(sections_raw, mapping)
    if new_sections != sections_raw:
        await conn.execute(
            "UPDATE paper_content SET sections_json = ? WHERE id = ?",
            (new_sections, paper_content_id),
        )
        rep.sections_json = True

    old_hash, new_hash = _rename_digest_file(source_dir, mapping, rep)

    async with conn.execute(
        "SELECT card_json, digest_hash FROM mechanism_cards WHERE paper_content_id = ?",
        (paper_content_id,),
    ) as cur:
        card_row = await cur.fetchone()
    if card_row is not None:
        card_raw, card_hash = str(card_row[0]), str(card_row[1])
        new_card = rename_card_json(card_raw, mapping)
        # Keep the cache hit: the card was built from the digest we just
        # renamed, so it stays current under the renamed digest's hash.
        resync = old_hash is not None and card_hash == old_hash and new_hash != old_hash
        if new_card != card_raw or resync:
            await conn.execute(
                "UPDATE mechanism_cards SET card_json = ?, digest_hash = ? "
                "WHERE paper_content_id = ?",
                (new_card, new_hash if resync else card_hash, paper_content_id),
            )
            rep.card = new_card != card_raw
            rep.card_hash_resynced = resync

    for old, new in mapping.items():
        for col, doc in (("source_section", "source_doc"), ("target_section", "target_doc")):
            cur2 = await conn.execute(
                f"UPDATE ideas SET {col} = ? WHERE {doc} = ? AND {col} = ?",  # noqa: S608 — fixed column names
                (new, paper_content_id, old),
            )
            rep.ideas += cur2.rowcount

    await _rename_decks(conn, paper_content_id, mapping, rep)
    await conn.commit()
    return rep


def _rename_digest_file(
    source_dir: str | None, mapping: Mapping[str, str], rep: RenameReport,
) -> tuple[str | None, str | None]:
    """Rename ``digest.json``; return its (old, new) card-cache hash."""
    if not source_dir:
        return None, None
    path = Path(source_dir) / "digest.json"
    if not path.is_file():
        return None, None
    from paperhub.ideation.cards import digest_hash  # local: avoid an import cycle
    from paperhub.models.slide_domain import PaperDigest

    try:
        old = PaperDigest.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        rep.notes.append("digest.json unreadable; left for a rebuild")
        return None, None
    renamed = PaperDigest.model_validate(rename_digest(old.model_dump(), mapping))
    if renamed != old:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(renamed.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        rep.digest = True
    return digest_hash(old), digest_hash(renamed)


async def _rename_decks(
    conn: aiosqlite.Connection, paper_id: int, mapping: Mapping[str, str],
    rep: RenameReport,
) -> None:
    async with conn.execute(
        "SELECT ds.id, ds.source_sections_json, ds.frame_tex, d.tex_path "
        "FROM deck_slides ds JOIN decks d ON d.id = ds.deck_id",
    ) as cur:
        rows = await cur.fetchall()
    tex_paths: set[str] = set()
    for sid, sources, frame, tex_path in rows:
        new_sources = rename_source_sections(str(sources or "[]"), paper_id, mapping)
        new_frame = rename_cite_lines(str(frame or ""), paper_id, mapping)
        if new_sources != (sources or "[]") or new_frame != (frame or ""):
            await conn.execute(
                "UPDATE deck_slides SET source_sections_json = ?, frame_tex = ? WHERE id = ?",
                (new_sources, new_frame, sid),
            )
            rep.deck_slides += 1
        if tex_path:
            tex_paths.add(str(tex_path))
    rep.deck_tex_files = _rename_cite_files(sorted(tex_paths), paper_id, mapping)


def _rename_cite_files(paths: list[str], paper_id: int, mapping: Mapping[str, str]) -> int:
    """Rewrite ``% cite:`` lines in the decks' TeX files; return files changed."""
    changed = 0
    for tp in paths:
        p = Path(tp)
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        new = rename_cite_lines(text, paper_id, mapping)
        if new != text:
            p.write_text(new, encoding="utf-8")
            changed += 1
    return changed
