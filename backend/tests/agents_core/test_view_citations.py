"""[view:P/N/x0,y0,x1,y1] citations: parsing, validation, the agent loop."""
from __future__ import annotations

import json
from typing import Any

import aiosqlite
import pytest

from paperhub.agents.core.agent import Final, run_core_agent
from paperhub.agents.core.citations import (
    cited_views,
    validate_view_citations,
    view_key,
)
from paperhub.agents.core.page_tools import PageImage
from paperhub.agents.core.tools import Tier, ToolContext, ToolOutcome, ToolRegistry, ToolSpec
from paperhub.tracing.tracer import Tracer

from .test_agent_loop import (
    AgentDeps,
    FakeAdapter,
    _answer,
    _calls,
    _drive,
    _enable,
    _rows,
    _settings,
    _setup,
)
from .test_read_tools import _paper


def test_cited_views_and_keys() -> None:
    text = "About 77% [view:20/5/120,340,560,720]; again [view: 20/5/120,340,560,720]."
    assert cited_views(text) == ["20/5/120,340,560,720"]
    assert view_key(20, 5, (120, 340, 560, 720)) == "20/5/120,340,560,720"


def test_validate_keeps_exact_viewed_tokens_only() -> None:
    text = ("A [view:20/5/120,340,560,720]. B [view:20/5/120,340,560,721]. "
            "C [view:20/6/120,340,560,720]. D [chunk:4] [fig:20/fig-003].")
    out, removed, snapped = validate_view_citations(text, {"20/5/120,340,560,720"})
    assert out == ("A [view:20/5/120,340,560,720]. B [unsourced:region_not_viewed]. "
                   "C [unsourced:region_not_viewed]. D [chunk:4] [fig:20/fig-003].")
    assert removed == ["20/5/120,340,560,721", "20/6/120,340,560,720"]
    assert snapped == []


def test_validate_snaps_a_box_inside_a_viewed_region_to_that_region() -> None:
    """A box the model wrote itself inside a region it did view cites what it
    saw: it becomes the smallest viewed region holding it, not a lost citation."""
    viewed = {"81/22/0,0,1000,1000", "81/22/300,380,700,640"}
    text = ("sim [view:81/22/330,400,370,860]; also [view:81/22/400,400,600,600]; "
            "no [view:81/23/10,10,20,20].")
    out, removed, snapped = validate_view_citations(text, viewed)
    assert out == ("sim [view:81/22/0,0,1000,1000]; also [view:81/22/300,380,700,640]; no [unsourced:region_not_viewed].")
    assert snapped == [("81/22/330,400,370,860", "81/22/0,0,1000,1000"),
                       ("81/22/400,400,600,600", "81/22/300,380,700,640")]
    assert removed == ["81/23/10,10,20,20"]


@pytest.mark.asyncio
async def test_loop_keeps_viewed_regions_and_carries_them_to_the_next_turn(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)
    pid, _ = await _paper(migrated_db, "2401.0011", {"Results": ["r"]})
    await _enable(migrated_db, pid)
    img = PageImage(paper=pid, page=5, box=(120, 340, 560, 720), mime="image/png",
                    data_b64="QQ==", size=(4, 4), sha256="c" * 64, dpi=300)

    async def zoom(_ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="<zoom>", images=[img], record={
            "paper": args["paper"], "page": 5, "box": [120, 340, 560, 720], "sha256": "c" * 64})

    reg = ToolRegistry()
    reg.register(ToolSpec("zoom", "d", {"type": "object"}, Tier.FREE, zoom))
    token = f"[view:{pid}/5/120,340,560,720]"
    adapter = FakeAdapter([
        _calls(("zoom", {"paper": pid, "page": 5, "box": [120, 340, 560, 720]})),
        _answer(f"About 77% {token}; and [view:{pid}/5/120,340,560,700]; not [view:{pid}/6/1,1,9,9]."),
    ])
    events = await _drive(migrated_db, tracer, adapter, reg)
    final = events[-1]
    assert isinstance(final, Final)
    assert final.text == f"About 77% {token}; and {token}; not [unsourced:region_not_viewed]."
    assert final.record["views"] == [f"{pid}/5/120,340,560,720"]
    assert final.record["cited_views"] == [f"{pid}/5/120,340,560,720"]
    step = (await _rows(migrated_db, "agent:step"))[-1]["result"]
    assert step["view_citations_removed"] == [f"{pid}/6/1,1,9,9"]
    assert step["view_citations_snapped"] == [
        [f"{pid}/5/120,340,560,700", f"{pid}/5/120,340,560,720"]]

    await migrated_db.execute("UPDATE runs SET agent_record_json = ? WHERE id = 1",
                              (json.dumps(final.record),))
    await migrated_db.execute(
        "INSERT INTO messages (session_id, role, content, run_id) VALUES (1, 'assistant', ?, 1)",
        (final.text,))
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    adapter2 = FakeAdapter([_answer(f"As shown {token}.")])
    deps = AgentDeps(adapter=adapter2, tracer=Tracer(migrated_db, run_id=2, branch=""),  # type: ignore[arg-type]
                     conn=migrated_db, settings=_settings(), registry=reg)
    state: dict[str, Any] = {"run_id": 2, "session_id": 1, "user_message": "again?",
                             "history": [{"role": "user", "content": "question"},
                                         {"role": "assistant", "content": final.text,
                                          "run_id": 1}]}
    events2 = [e async for e in run_core_agent(state, deps)]  # type: ignore[arg-type]
    final2 = events2[-1]
    assert isinstance(final2, Final) and final2.text == f"As shown {token}."
    assert any(f"views: {pid}/5/120,340,560,720" in str(m.get("content"))
               for m in adapter2.calls[0]["messages"])
