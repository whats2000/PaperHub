"""XML pieces the core agent sees for the user's notes: shared by the note
tools and the session state (``context.py``). Note text is data, escaped."""
from __future__ import annotations

import html


def esc(value: object) -> str:
    """``value`` escaped for an XML attribute."""
    return html.escape(str(value), quote=True)


def title_xml(title: str) -> str:
    return f"<note_title>{html.escape(title, quote=False)}</note_title>"
