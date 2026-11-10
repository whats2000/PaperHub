"""Paper reading tools for the core agent (all ``Tier.FREE``)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from typing import Any

from paperhub.agents.core.figures import (
    encode_figure,
    figure_content,
    figures_tables_text,
    load_figures,
    resolve_figure,
)
from paperhub.agents.core.page_tools import (
    view_chunk_tool,
    view_figure_page,
    view_page_tool,
    zoom_tool,
)
from paperhub.agents.core.tools import (
    Tier,
    ToolContext,
    ToolOutcome,
    ToolRegistry,
    ToolSpec,
    error_outcome,
)
from paperhub.agents.paper_qa_subagent import (
    _list_sections,
    _read_layout_object,
    _read_section,
)
from paperhub.agents.research_tools import _to_fts5_query

READ_PAGE_CHUNKS = 12
READ_CHUNKS_MAX_IDS = 30
SNIPPET_CHARS = 300
SEARCH_LIMIT_MAX = 50
# Han, kana and hangul runs: one unicode61 token each, so matched as substrings.
_CJK_RUN = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]+")
NO_PAPERS = (
    "No papers are enabled in this chat; use search_papers or ask the user "
    "to add one."
)


def _paper_error(ctx: ToolContext, paper: Any) -> ToolOutcome | None:
    """Return an error outcome unless ``paper`` is an enabled paper id."""
    if not ctx.enabled_papers:
        return error_outcome(NO_PAPERS)
    if (
        isinstance(paper, int)
        and not isinstance(paper, bool)
        and any(p == paper for p, _ in ctx.enabled_papers)
    ):
        return None
    listing = "; ".join(f"{p}: {t}" for p, t in ctx.enabled_papers)
    return error_outcome(
        f"Paper {paper!r} is not enabled in this chat. Enabled papers: {listing}."
    )


def chunk_block(chunk_id: int, text: str, page: int | None) -> str:
    block = f'<chunk id="{chunk_id}">\n{text}\n</chunk>'
    return f"{block} (p.{page})" if page is not None else block


# The content formatters below are shared with the feedback export, which
# rebuilds a recorded turn's tool results from the chunk ids its trace holds.


def format_section_page(blocks: list[str], page: int, pages: int) -> str:
    return "\n\n".join(blocks) + f"\n\npage {page} of {pages}"


def format_hit(
    chunk_id: int, paper: int, section: Any, text: str, page: int | None,
) -> str:
    snippet = text[:SNIPPET_CHARS] + ("…" if len(text) > SNIPPET_CHARS else "")
    where = f"paper {paper}, section {section!r}"
    if page is not None:
        where += f", p.{page}"
    return f'<hit chunk="{chunk_id}" {where}>\n{snippet}\n</hit>'


def format_read_chunks(blocks: list[str], unknown: list[int]) -> str:
    parts = list(blocks)
    if unknown:
        parts.append(f"Unknown chunk id(s): {', '.join(map(str, unknown))}.")
    return "\n\n".join(parts)


async def _list_sections_tool(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    paper = args["paper"]
    if (err := _paper_error(ctx, paper)) is not None:
        return err
    text = await _list_sections(paper_content_id=paper, conn=ctx.conn)
    return ToolOutcome(content=text, record={"paper": paper, "sections": text})


async def _read_section_tool(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    paper = args["paper"]
    if (err := _paper_error(ctx, paper)) is not None:
        return err
    section = str(args["section"])
    page = args.get("page", 1)
    if not isinstance(page, int) or page < 1:
        return error_outcome("page must be a positive integer.")
    body, picks = await _read_section(
        paper_content_id=paper, name=section, conn=ctx.conn
    )
    if not picks:
        return error_outcome(json.loads(body)["error"])
    pages = math.ceil(len(picks) / READ_PAGE_CHUNKS)
    if page > pages:
        return error_outcome(
            f"page {page} is out of range; this section has {pages} page(s)."
        )
    window = picks[(page - 1) * READ_PAGE_CHUNKS : page * READ_PAGE_CHUNKS]
    ids = [p.chunk_id for p in window]
    return ToolOutcome(
        content=format_section_page(
            [chunk_block(p.chunk_id, p.text, p.page) for p in window], page, pages),
        record={"paper": paper, "section": section, "chunk_ids": ids,
                "page": page, "pages": pages},
        chunk_ids_read=ids,
    )


async def _search_chunks_tool(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    query = str(args["query"])
    limit = args.get("limit", 10)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        limit = 10
    limit = min(limit, SEARCH_LIMIT_MAX)
    named = args.get("papers")
    if named:
        items = named if isinstance(named, list) else [named]
        bad = [p for p in items if not isinstance(p, int) or isinstance(p, bool)]
        if bad or not isinstance(named, list):
            return error_outcome(
                "papers must be a list of integer paper ids; "
                f"got invalid value(s): {bad or [named]!r}."
            )
        scope = list(named)
    elif ctx.enabled_papers:
        scope = [p for p, _ in ctx.enabled_papers]
    else:
        return error_outcome(NO_PAPERS)
    fts = _to_fts5_query(query)
    if not fts:
        return error_outcome("The query has no searchable words.")
    marks = ",".join("?" for _ in scope)

    async def fts_rows(expr: str) -> list[Any]:
        async with ctx.conn.execute(
            "SELECT c.id, c.paper_content_id, c.section, c.text, c.page "  # noqa: S608
            "FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
            f"WHERE chunks_fts MATCH ? AND c.paper_content_id IN ({marks}) "
            "ORDER BY rank LIMIT ?",
            (expr, *scope, limit),
        ) as cur:
            return list(await cur.fetchall())

    # Every term first; else any term (bm25 ranks chunks with more of them
    # higher); else, for CJK runs (unicode61 keeps a run as one token), a
    # substring match.
    match, sent = "all_terms", fts
    rows = await fts_rows(fts)
    if not rows and " AND " in fts:
        match, sent = "any_term", fts.replace(" AND ", " OR ")
        rows = await fts_rows(sent)
    cjk = _CJK_RUN.findall(query)
    if not rows and cjk:
        match, sent = "substring", " OR ".join(cjk)
        likes = " OR ".join("c.text LIKE ?" for _ in cjk)
        async with ctx.conn.execute(
            "SELECT c.id, c.paper_content_id, c.section, c.text, c.page "  # noqa: S608
            f"FROM chunks c WHERE c.paper_content_id IN ({marks}) AND ({likes}) "
            "ORDER BY c.id LIMIT ?",
            (*scope, *(f"%{t}%" for t in cjk), limit),
        ) as cur:
            rows = list(await cur.fetchall())
    if not rows:
        match = "none"
    record: dict[str, Any] = {
        "query_as_sent": sent, "match": match, "papers": scope,
        "hit_ids": [r[0] for r in rows],
    }
    if not rows:
        return ToolOutcome(content="No matching chunks.", record=record)
    lines = [format_hit(cid, pid, section, text, page)
             for cid, pid, section, text, page in rows]
    return ToolOutcome(content="\n\n".join(lines), record=record)


async def _read_chunks_tool(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    raw = args["ids"]
    if not isinstance(raw, list) or not all(isinstance(i, int) for i in raw):
        return error_outcome("ids must be a list of integer chunk ids.")
    if not raw:
        return error_outcome("ids is empty.")
    if len(raw) > READ_CHUNKS_MAX_IDS:
        return error_outcome(
            f"Too many ids; read at most {READ_CHUNKS_MAX_IDS} chunks per call."
        )
    marks = ",".join("?" for _ in raw)
    async with ctx.conn.execute(
        f"SELECT id, text, page FROM chunks WHERE id IN ({marks})",  # noqa: S608
        tuple(raw),
    ) as cur:
        found = {r[0]: (r[1], r[2]) for r in await cur.fetchall()}
    unique = list(dict.fromkeys(raw))
    ordered = [i for i in unique if i in found]
    unknown = [i for i in unique if i not in found]
    return ToolOutcome(
        content=format_read_chunks(
            [chunk_block(i, found[i][0], found[i][1]) for i in ordered], unknown),
        record={"chunk_ids": ordered, "unknown_ids": unknown},
        chunk_ids_read=ordered,
    )


async def _list_figures_tables_tool(
    ctx: ToolContext, args: dict[str, Any]
) -> ToolOutcome:
    paper = args["paper"]
    if (err := _paper_error(ctx, paper)) is not None:
        return err
    text, labels, figure_ids = await figures_tables_text(
        paper_content_id=paper, conn=ctx.conn)
    return ToolOutcome(content=text, record={
        "paper": paper, "labels": labels, "figure_ids": figure_ids,
        # lets the feedback export tell a re-ingested figure list apart
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    })


async def _read_layout_object_tool(
    ctx: ToolContext, args: dict[str, Any]
) -> ToolOutcome:
    paper = args["paper"]
    if (err := _paper_error(ctx, paper)) is not None:
        return err
    body, pick = await _read_layout_object(
        paper_content_id=paper, label=str(args["label"]), conn=ctx.conn
    )
    if pick is None:
        return error_outcome(json.loads(body)["error"])
    return ToolOutcome(
        content=body,
        record={"paper": paper, "label": args["label"], "chunk_ids": [pick.chunk_id]},
        chunk_ids_read=[pick.chunk_id],
    )


async def _view_figure_tool(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    paper, ref = args.get("paper"), str(args.get("figure", ""))
    if (err := _paper_error(ctx, paper)) is not None:
        return err
    assert isinstance(paper, int)
    src, figures = await load_figures(ctx.conn, paper)
    if src is None or not figures:
        return error_outcome(f"Paper {paper} has no figure images.")
    fig = resolve_figure(figures, ref)
    if fig is None:
        ids = ", ".join(f.id for f in figures)
        return error_outcome(
            f"No figure {ref!r} in paper {paper}. Figure ids: {ids}.")
    if fig.page is not None:
        head = figure_content(paper, fig).replace(
            "\nThe image is in the next message.", "")
        paged = await view_figure_page(ctx, paper, fig.id, fig.page, fig.bbox, head)
        if paged is not None:
            paged.record["ref"] = ref
            return paged
    try:
        image = await asyncio.to_thread(encode_figure, paper, src, fig)
    except OSError as exc:
        return error_outcome(f"Cannot open the image of {fig.id}: {exc}.")
    return ToolOutcome(
        content=figure_content(paper, fig),
        record={"paper": paper, "figure": fig.id, "ref": ref,
                "image_path": fig.image_path, "sha256": image.sha256,
                "page": fig.page, "size": list(image.size), "mime": image.mime},
        images=[image],
    )


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


_PAPER = {"type": "integer", "description": "paper_content_id of an enabled paper."}


def register_read_tools(registry: ToolRegistry, *, vision: bool = True) -> None:
    """``vision=False`` (a text-only model) leaves out every image tool."""
    specs = [
        ToolSpec(
            "list_sections",
            "List a paper's sections with chunk and token counts.",
            _obj({"paper": _PAPER}, ["paper"]),
            Tier.FREE, _list_sections_tool,
        ),
        ToolSpec(
            "read_section",
            f"Read a section, {READ_PAGE_CHUNKS} chunks per page. Chunks carry "
            "ids you can cite as [chunk:N].",
            _obj({"paper": _PAPER, "section": {"type": "string"},
                  "page": {"type": "integer", "minimum": 1}},
                 ["paper", "section"]),
            Tier.FREE, _read_section_tool,
        ),
        ToolSpec(
            "search_chunks",
            "Keyword search over paper chunks (enabled papers unless `papers` "
            "is given); returns chunk ids and snippets. Use a few key terms in "
            "the paper's language (usually English): chunks with every term "
            "rank first, else any term; CJK terms match as substrings.",
            _obj({"query": {"type": "string"},
                  "papers": {"type": "array", "items": {"type": "integer"}},
                  "limit": {"type": "integer", "minimum": 1}}, ["query"]),
            Tier.FREE, _search_chunks_tool,
        ),
        ToolSpec(
            "read_chunks",
            f"Read the full text of up to {READ_CHUNKS_MAX_IDS} chunks by id "
            "(including ids from earlier turns).",
            _obj({"ids": {"type": "array", "items": {"type": "integer"}}}, ["ids"]),
            Tier.FREE, _read_chunks_tool,
        ),
        ToolSpec(
            "list_figures_tables",
            "List a paper's figures and tables with their citable chunks.",
            _obj({"paper": _PAPER}, ["paper"]),
            Tier.FREE, _list_figures_tables_tool,
        ),
        ToolSpec(
            "read_layout_object",
            "Read a figure or table by its label, e.g. 'Table 1'.",
            _obj({"paper": _PAPER, "label": {"type": "string"}}, ["paper", "label"]),
            Tier.FREE, _read_layout_object_tool,
        ),
    ]
    if vision:
        specs.append(ToolSpec(
            "view_figure",
            "Look at a figure's image. Use it when the answer depends on what "
            "the figure shows (bar or point values, counts, trends, labels), "
            "not only its caption. `figure` is an id from list_figures_tables "
            "(e.g. 'fig-003') or the caption's number (e.g. 'Figure 3').",
            _obj({"paper": _PAPER, "figure": {"type": "string"}}, ["paper", "figure"]),
            Tier.FREE, _view_figure_tool,
        ))
    if vision:
        specs += [
            ToolSpec(
                "view_page",
                "Look at a page of the paper as an image (figures on it are listed "
                "with their boxes). The result's cite token is the page.",
                _obj({"paper": _PAPER, "page": {"type": "integer", "minimum": 1}},
                     ["paper", "page"]),
                Tier.FREE, view_page_tool,
            ),
            ToolSpec(
                "zoom",
                "Look closely at a region of a page: box is [x0, y0, x1, y1] in 0-1000 "
                "page coordinates (x right, y down). Use it to read small print, a "
                "panel, a formula or a table cell. The result's cite token is that region.",
                _obj({"paper": _PAPER, "page": {"type": "integer", "minimum": 1},
                      "box": {"type": "array", "items": {"type": "integer"},
                              "minItems": 4, "maxItems": 4}},
                     ["paper", "page", "box"]),
                Tier.FREE, zoom_tool,
            ),
            ToolSpec(
                "view_chunk",
                "Look at the page region a chunk was read from (PDF papers), e.g. "
                "to read a formula or table as printed rather than as transcribed.",
                _obj({"id": {"type": "integer"}}, ["id"]),
                Tier.FREE, view_chunk_tool,
            ),
        ]
    for s in specs:
        registry.register(s)
