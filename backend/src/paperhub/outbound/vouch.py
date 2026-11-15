"""Proof that a query already passed the outbound checkpoint (Plan H5a).

The paper-search pipeline sends the in-process ``papers.search_semantic_scholar``
tool an ``outbound_token`` = HMAC-SHA256(process secret, query). The tool then
skips its own defensive sensitive-term check. The secret is random per
process and never leaves it, so no external MCP client can forge a token; a
token from another worker simply fails and the cheap check runs again.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

__all__ = ["vouch", "vouched"]

_SECRET = secrets.token_bytes(32)


def vouch(query: str) -> str:
    """The token for ``query`` (exact text, as sent)."""
    return hmac.new(_SECRET, query.encode("utf-8"), hashlib.sha256).hexdigest()


def vouched(query: str, token: str | None) -> bool:
    """True when ``token`` was issued by this process for exactly ``query``."""
    if not token:
        return False
    # Bytes, not str: compare_digest raises TypeError on non-ASCII str, and a
    # bad token must fall back to the term check, never crash the tool.
    return hmac.compare_digest(vouch(query).encode("ascii"),
                               token.encode("utf-8", "surrogatepass"))
