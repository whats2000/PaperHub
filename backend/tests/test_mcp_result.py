"""Unit contract for :func:`paperhub.agents._mcp_result.normalize_mcp_result`.

The canonical MCP result normaliser (shared by ``sql_agent`` and
``memory_node``). The integration shapes are exercised in
``test_research_pipeline.py``; these are the focused unit-level contract cases
(previously colocated with the now-removed SQL pipeline tests).
"""
from paperhub.agents._mcp_result import normalize_mcp_result, split_mcp_result


def test_normalize_mcp_result_passthrough_dict() -> None:
    """A multi-key dict is returned unchanged (not a string, not unwrapped)."""
    d = {"columns": ["n"], "rows": [[3]]}
    assert normalize_mcp_result(d) is d


def test_normalize_mcp_result_passthrough_list() -> None:
    """Lists are returned unchanged (not a string)."""
    lst = ["papers", "paper_content"]
    assert normalize_mcp_result(lst) is lst


def test_normalize_mcp_result_json_string_dict() -> None:
    """A JSON-encoded dict string is parsed into a dict."""
    raw = '{"columns":["n"],"rows":[[3]]}'
    assert normalize_mcp_result(raw) == {"columns": ["n"], "rows": [[3]]}


def test_normalize_mcp_result_json_string_list() -> None:
    """A JSON-encoded list string is parsed into a list."""
    raw = '["papers"]'
    assert normalize_mcp_result(raw) == ["papers"]


def test_normalize_mcp_result_non_json_string_returned_unchanged() -> None:
    """A plain (non-JSON) string not starting with { or [ is returned as-is.

    ``normalize_mcp_result`` only attempts ``json.loads`` when the stripped
    string starts with '{' or '['; anything else is returned verbatim.
    """
    assert normalize_mcp_result("oops") == "oops"


def test_normalize_mcp_result_unwraps_result_envelope() -> None:
    """A single-key ``{"result": X}`` envelope (FastMCP list-return shape) is
    unwrapped to ``X``."""
    assert normalize_mcp_result({"result": ["a", "b"]}) == ["a", "b"]


def test_meta_envelope_is_unwrapped_and_split() -> None:
    """``{"result": X, "_meta": M}`` (meta-in-payload rule) unwraps to X for
    normalize and to (X, M) for split; plain shapes split to ({} meta)."""
    env = {"result": [{"t": 1}], "_meta": {"http_status": 200}}
    assert normalize_mcp_result(env) == [{"t": 1}]
    assert split_mcp_result(env) == ([{"t": 1}], {"http_status": 200})
    assert split_mcp_result('{"result": [], "_meta": {"count": 0}}') == ([], {"count": 0})
    assert split_mcp_result({"result": [1]}) == ([1], {})
    assert split_mcp_result([1]) == ([1], {})
