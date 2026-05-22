"""Shared MCP result normaliser (SRS v2.16, Plan E wire-fix).

FastMCP's ``json_response=True`` / ``stateless_http=True`` combination surfaces
tool payloads in two distinct shapes depending on whether the handler returns a
``list`` or a ``dict``:

* **dict** return → arrives as the dict (e.g. ``{"columns": [...], "rows": [...]}``).
* **list** return → arrives wrapped in a ``{"result": [...]}`` envelope
  (e.g. ``memory.recall``).

In addition, some FastMCP transports / versions serialise the payload as a
JSON *string* rather than a parsed object — the existing
``sql_agent._normalize_mcp_result`` already handles that.

This module consolidates both normalisation steps into a single helper so
``sql_agent`` and ``memory_node`` share identical semantics.  The rules, in
order:

1. If *raw* is a **str** that starts with ``{`` or ``[``, try ``json.loads``.
   Failure → return as-is (non-JSON string passthrough).
2. If the result is a **dict** whose *only* key is ``"result"``, unwrap it and
   return ``result["result"]``.  This handles the FastMCP list-return envelope.
   Any dict with *more* than one key (e.g. ``{"columns": …, "rows": …}``) is
   returned unchanged — unwrapping there would destroy the payload.
3. Otherwise return as-is.
"""
from __future__ import annotations

import json
from typing import Any


def normalize_mcp_result(raw: Any) -> Any:
    """Normalise a raw value returned from ``MCPRegistry.call``.

    Handles:
    * JSON-string → parsed (str → dict/list/…)
    * ``{"result": X}`` single-key envelope → ``X``  (FastMCP list-return shape)
    * Everything else → identity

    This function is **idempotent**: calling it twice on an already-normalised
    value returns the same value without error.
    """
    # Step 1 — parse JSON strings.
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped[:1] in ("{", "["):
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError:
                return raw
        else:
            return raw

    # Step 2 — unwrap {"result": X} envelope emitted by FastMCP for list returns.
    if isinstance(raw, dict) and set(raw.keys()) == {"result"}:
        return raw["result"]

    return raw
