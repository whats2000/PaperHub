import json
import struct
import zlib
from typing import Any

import pytest

from paperhub.cli import model_check as mc
from paperhub.llm.adapter import ToolCall, ToolTurn


class _StubAdapter:
    """No network: every check passes except the one named in ``fail``."""

    def __init__(self, fail: str | None = None) -> None:
        self.fail = fail
        self.tool_calls_made = 0

    async def structured(self, *, response_model: Any, **_k: Any) -> Any:
        if self.fail == "structured":
            raise ValueError("bad json")
        return response_model(name="Alice", age=30)

    async def tool_turn(self, *, messages: list[dict[str, Any]], tools: list[Any],
                        **_k: Any) -> ToolTurn:
        if tools:
            if self.fail == "tools":
                return ToolTurn("I will not call tools", [])
            if not any(m["role"] == "tool" for m in messages):
                return ToolTurn("", [ToolCall("c1", "get_code", {})])
            return ToolTurn("", [ToolCall("c2", "use_code", {"code": "K-17"})])
        content = messages[0]["content"]
        if isinstance(content, list):
            return ToolTurn("7")
        return ToolTurn("48213")

    def stream(self, **_k: Any) -> Any:  # pragma: no cover - unused
        raise NotImplementedError


def test_digit_png_is_a_valid_64px_png() -> None:
    png = mc.digit_png("7")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", png[16:24])
    assert (w, h) == (64, 64)
    idat = png.index(b"IDAT")
    n = struct.unpack(">I", png[idat - 4:idat])[0]
    assert len(zlib.decompress(png[idat + 4: idat + 4 + n])) == 64 * 65


def test_model_check_reports_failures(capsys: pytest.CaptureFixture[str]) -> None:
    code = mc.main(["--model", "ollama/x", "--context", "200"], adapter=_StubAdapter("tools"))
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL  ollama/x  tools" in out
    assert "PASS  ollama/x  structured" in out
    assert "failing: ollama/x:tools" in out


def test_model_check_all_pass_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = mc.main(["--model", "ollama/x", "--context", "200", "--json"],
                   adapter=_StubAdapter())
    rows = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [r["check"] for r in rows] == list(mc.CHECKS)
    assert all(r["ok"] for r in rows)
