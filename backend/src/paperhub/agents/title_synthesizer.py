"""F4.5 talk-title synthesizer.

Multi-paper decks need a concise talk title (used by build_title_metadata
to fill \\title{} in the preamble). The user message is too verbose to be
the title verbatim. This module takes the gathered bundles + the user
message and returns a <=60-char talk title.

Single-paper decks DON'T need this — build_title_metadata uses the paper's
own title and ignores the talk_title arg.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.models.slide_domain import PaperContextBundle

LlmAcompletion = Callable[..., Awaitable[Any]]


_FALLBACK_TITLE = "Conference Talk"


async def synthesize_talk_title(
    *,
    bundles: list[PaperContextBundle],
    user_message: str,
    response_language: str,
    model: str,
    registry: PromptRegistry | None = None,
    llm_acompletion: LlmAcompletion | None = None,
) -> str:
    """Return a concise (<=60 char) talk title derived from the gathered bundles.

    Used by report_graph._generate for multi-paper decks ONLY. Single-paper
    decks let build_title_metadata fall back to the paper's own title.

    Fails gracefully to the first 60 chars of user_message on any LLM error
    (we never want title-synthesis to block deck generation).
    """
    reg = registry or PromptRegistry()
    prompt = reg.get("slides_title_synthesizer/v1")
    if llm_acompletion is None:
        from paperhub.outbound.llm_destinations import acompletion_noted

        llm_acompletion = acompletion_noted

    paper_titles = "; ".join(f"[{b.paper_idx}] {b.title}" for b in bundles)
    narrative_summaries = "\n\n".join(
        f"[{b.paper_idx}] {b.narrative_summary[:400]}" for b in bundles
    )
    user = prompt.user_template.format(
        paper_titles_block=paper_titles,
        narratives_block=narrative_summaries,
        user_message=user_message[:300],
        response_language=response_language,
    )
    messages = [
        {"role": "system", "content": prompt.system},
        {"role": "user", "content": user},
    ]
    try:
        resp = await llm_acompletion(model=model, messages=messages)
        title = str(resp["choices"][0]["message"]["content"] or "").strip()
        # Trim quotes if the LLM wrapped the title; collapse whitespace; cap to 60.
        title = title.strip('"').strip("'").strip("「」").strip()
        title = " ".join(title.split())
        if not title:
            raise ValueError("empty title")
        return title[:60]
    except Exception:
        # Soft-fail to the user message truncation (matches the previous behavior).
        fallback = (user_message or "").strip()[:60] or _FALLBACK_TITLE
        return fallback
