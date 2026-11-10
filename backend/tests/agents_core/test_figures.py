"""The core agent's figure images: listing, ``view_figure`` and the image
message that carries the pixels to the model."""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import aiosqlite
import pytest
from PIL import Image

from paperhub.agents.core.figures import (
    IMAGE_MAX_SIDE,
    figures_tables_text,
    images_message,
    resolve_figure,
)
from paperhub.agents.core.read_tools import register_read_tools
from paperhub.agents.core.tools import ToolContext, ToolRegistry
from paperhub.llm.adapter import ToolCall
from paperhub.pipelines.paper_asset import FigureAsset
from paperhub.tracing.tracer import Tracer


def _png(path: Path, size: tuple[int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (20, 120, 60)).save(path, format="PNG")


async def _paper_with_figures(
    conn: aiosqlite.Connection, root: Path, figures: list[dict[str, Any]],
    *, layout: list[dict[str, Any]] | None = None, size: tuple[int, int] = (200, 100),
) -> int:
    asset = root / "asset"
    for f in figures:
        if f["image_path"]:
            _png(asset / f["image_path"], size)
    asset.mkdir(parents=True, exist_ok=True)
    (asset / "figures.json").write_text(json.dumps(figures), encoding="utf-8")
    await conn.execute(
        "INSERT INTO paper_content (content_key, kind, sha256, title, source_path, "
        "source_dir_path, html_path, layout_json) "
        "VALUES (?, 'pdf_upload', ?, 'LIVECell', '/x.pdf', ?, '/x', ?)",
        (f"sha256:{root.name}", root.name, str(root),
         json.dumps(layout) if layout is not None else None),
    )
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    await conn.commit()
    return int(row[0])


def _fig(fid: str, caption: str, page: int | None = 4) -> dict[str, Any]:
    return {"id": fid, "caption": caption, "page": page, "section": None,
            "image_path": f"figures/{fid}.png"}


def _ctx(
    conn: aiosqlite.Connection, tracer: Tracer, enabled: list[tuple[int, str]],
) -> ToolContext:
    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None

    return ToolContext(
        conn=conn, tracer=tracer, session_id=1, run_id=1,
        settings=MagicMock(), adapter=MagicMock(), emit=emit,
        enabled_papers=enabled, history=[], state={},
    )


def _reg() -> ToolRegistry:
    r = ToolRegistry()
    register_read_tools(r)
    return r


def test_resolve_figure_by_id_or_by_the_caption_number() -> None:
    figs = [
        FigureAsset("fig-000", "", 0, None, "figures/fig-000.png"),
        FigureAsset("fig-003", "Fig. 3 | Performance evaluation", 4, None,
                    "figures/fig-003.png"),
        FigureAsset("fig-004", "Figure 13: Ablation", 9, None, "figures/fig-004.png"),
    ]
    assert resolve_figure(figs, "fig-003") is figs[1]
    assert resolve_figure(figs, "FIG-003") is figs[1]
    assert resolve_figure(figs, "Figure 3") is figs[1]
    assert resolve_figure(figs, "Fig. 3") is figs[1]
    assert resolve_figure(figs, "fig 3") is figs[1]
    assert resolve_figure(figs, "Figure 13") is figs[2]
    assert resolve_figure(figs, "Figure 1") is None
    assert resolve_figure(figs, "Table 3") is None


@pytest.mark.asyncio
async def test_list_figures_tables_lists_figure_images_even_without_layout(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    # A LaTeX paper: no layout index, but its asset holds figure images.
    pid = await _paper_with_figures(migrated_db, tmp_path / "p", [
        _fig("fig-001", "", None), _fig("fig-002", "Latent actions across robots.", None),
    ])
    out = await _reg().run(_ctx(migrated_db, fake_tracer, [(pid, "T")]), ToolCall(
        id="c", name="list_figures_tables", arguments={"paper": pid}))
    assert "fig-001" in out.content and "fig-002" in out.content
    assert "Latent actions across robots." in out.content
    assert "view_figure" in out.content
    assert out.record["figure_ids"] == ["fig-001", "fig-002"]
    text, _labels, _ids = await figures_tables_text(paper_content_id=pid, conn=migrated_db)
    assert text == out.content  # the feedback export rebuilds the same text


@pytest.mark.asyncio
async def test_view_figure_returns_the_image_and_records_its_file(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    pid = await _paper_with_figures(
        migrated_db, tmp_path / "p", [_fig("fig-003", "Fig. 3 | Bar charts of AP")],
        size=(3000, 1500))
    out = await _reg().run(_ctx(migrated_db, fake_tracer, [(pid, "T")]), ToolCall(
        id="c", name="view_figure", arguments={"paper": pid, "figure": "Figure 3"}))
    assert not out.record.get("error")
    assert out.record["figure"] == "fig-003"
    assert out.record["image_path"] == "figures/fig-003.png"
    assert len(out.record["sha256"]) == 64
    assert 'id="fig-003"' in out.content and "Fig. 3 | Bar charts of AP" in out.content
    assert len(out.images) == 1
    img = out.images[0]
    # Downscaled to the long-side cap, aspect ratio kept.
    assert max(img.size) == IMAGE_MAX_SIDE and img.size[0] == 2 * img.size[1]
    decoded = Image.open(io.BytesIO(base64.b64decode(img.data_b64)))
    assert decoded.size == img.size


@pytest.mark.asyncio
async def test_view_figure_unknown_ref_lists_the_valid_ids(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    pid = await _paper_with_figures(
        migrated_db, tmp_path / "p", [_fig("fig-001", "Figure 1: Overview")])
    out = await _reg().run(_ctx(migrated_db, fake_tracer, [(pid, "T")]), ToolCall(
        id="c", name="view_figure", arguments={"paper": pid, "figure": "Figure 9"}))
    assert out.record.get("error") and "fig-001" in out.content
    assert out.images == []


@pytest.mark.asyncio
async def test_view_figure_refuses_a_paper_not_in_the_chat(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    pid = await _paper_with_figures(
        migrated_db, tmp_path / "p", [_fig("fig-001", "Figure 1: Overview")])
    out = await _reg().run(_ctx(migrated_db, fake_tracer, [(pid + 1, "T")]), ToolCall(
        id="c", name="view_figure", arguments={"paper": pid, "figure": "fig-001"}))
    assert out.record.get("error") and out.images == []


def test_vision_off_does_not_register_view_figure() -> None:
    r = ToolRegistry()
    register_read_tools(r, vision=False)
    names = [s["function"]["name"] for s in r.schemas()]
    assert "view_figure" not in names and "list_figures_tables" in names


def test_images_message_is_one_user_message_with_text_and_image_parts() -> None:
    from paperhub.agents.core.figures import FigureImage

    img = FigureImage(paper=3, figure_id="fig-003", caption="Fig. 3", page=4,
                      mime="image/png", data_b64="QUJD", size=(10, 5), sha256="0" * 64)
    msg = images_message([img])
    assert msg["role"] == "user"
    kinds = [p["type"] for p in msg["content"]]
    assert kinds.count("image_url") == 1 and "text" in kinds
    url = next(p for p in msg["content"] if p["type"] == "image_url")["image_url"]["url"]
    assert url == "data:image/png;base64,QUJD"
