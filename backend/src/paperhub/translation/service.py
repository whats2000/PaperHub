"""Translate paper blocks with placeholder checks, escalation and caching (Plan H1).

A translation batch is a pipeline call, not a chat turn: no ``runs`` row and no
tracer step. Failures go to the backend log (paper id, block keys, language,
provider error).

Escalation per failed block (placeholder mismatch, an untranslated echo of the
source, missing from the reply, or the call raised): retry alone on ``model`` →
alone on ``flagship_model`` → ``unavailable``. An echo is never cached, and a
cached row that is one (written before the check existed) is a miss. The whole
request shares one time budget (``budget_s``): a call that would overrun it is
cut off and its blocks end ``unavailable``. ``LiteLlmAdapter.structured`` has its own availability
fallback (a non-small model that is down is downgraded to the small tier for
that call); the service does not add another one. So the flagship attempt may
in rare outages be served by the small tier; the placeholder check still
guards the result, and ``model`` reports the tier the service requested.

Context (``<previous_translation>``) is the translation of the block before the
one being sent. In request order (blocks are adjacent) that is the previous
block's result; for the first block it is derivable only for HTML keys
(``h:<n>`` → cached ``h:<n-1>``). A PDF key ``p:<block_id>`` names no
predecessor, so the first PDF block gets no context.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
from xml.sax.saxutils import escape

import aiosqlite

from paperhub.agents.memory_recall import build_active_memory_block
from paperhub.db.connection import write_transaction
from paperhub.llm.adapter import LlmAdapter
from paperhub.translation.cache import CachedRow, get_cached, put_cached
from paperhub.translation.models import (
    TranslateBlock,
    TranslatedBlocks,
    TranslateRequest,
    TranslateResponse,
    TranslateResult,
)
from paperhub.translation.placeholders import (
    br_position_intact,
    normalize_language,
    placeholders_intact,
    source_hash,
)
from paperhub.translation.quality import is_echo, tidy_cjk_spacing

logger = logging.getLogger(__name__)

SLOT = "translate_blocks/v1"

# Time budget for one request's model calls (primary batch, retries and
# flagship together), so one pathological block can't hold a call for minutes.
DEFAULT_BUDGET_S = 60.0

# (text, model) of a clean translation, or None when it ended unavailable.
_Outcome = tuple[str, str] | None


def _problem(source: str, output: str | None, language: str) -> str | None:
    """Why ``output`` is not a usable translation of ``source``, or ``None``."""
    if output is None:
        return "missing from reply"
    if not (placeholders_intact(source, output) and br_position_intact(source, output)):
        return "placeholder mismatch"
    if is_echo(source, output, language):
        return "echo (untranslated source)"
    return None


def _blocks_xml(blocks: list[TranslateBlock]) -> str:
    return "\n".join(
        f'<block key="{escape(b.key, {chr(34): "&quot;"})}">{escape(b.text)}</block>'
        for b in blocks
    )


def _usable(row: CachedRow | None, block: TranslateBlock, language: str) -> bool:
    """A cached row for this exact source that is not an untranslated echo."""
    return (row is not None and row.source_hash == source_hash(block.text)
            and not is_echo(block.text, row.text, language))


def _html_predecessor(key: str) -> str | None:
    """``h:<n>`` → ``h:<n-1>``; ``None`` for PDF keys or ``h:0``."""
    prefix, _, idx = key.partition(":")
    if prefix != "h" or not idx.isdigit() or int(idx) == 0:
        return None
    return f"h:{int(idx) - 1}"


class TranslationService:
    def __init__(
        self,
        llm: LlmAdapter,
        *,
        model: str,
        flagship_model: str,
        max_in_flight: int = 2,
        budget_s: float = DEFAULT_BUDGET_S,
    ) -> None:
        self._llm = llm
        self._model = model
        self._flagship = flagship_model
        self._max_in_flight = max_in_flight
        self._budget_s = budget_s
        self._sems: dict[int, asyncio.Semaphore] = {}
        # (paper, key, language, source_hash) -> the owning request's outcome.
        self._inflight: dict[tuple[int, str, str, str], asyncio.Future[_Outcome]] = {}

    def _sem(self, paper_content_id: int) -> asyncio.Semaphore:
        sem = self._sems.get(paper_content_id)
        if sem is None:
            sem = self._sems[paper_content_id] = asyncio.Semaphore(self._max_in_flight)
        return sem

    async def translate(
        self, conn: aiosqlite.Connection, req: TranslateRequest,
    ) -> TranslateResponse:
        language = normalize_language(req.language)
        pid = req.paper_content_id
        # Covers every active global memory, not only terminology: any memory
        # change marks the cached blocks stale (they still show; re-translate).
        terminology = await build_active_memory_block(conn, session_id=None)
        memory_hash = hashlib.sha256(terminology.encode("utf-8")).hexdigest()

        keys = [b.key for b in req.blocks]
        first_prev = _html_predecessor(keys[0])
        lookup = keys + ([first_prev] if first_prev and first_prev not in keys else [])
        cached = await get_cached(conn, pid, lookup, language)

        results: dict[str, TranslateResult] = {}
        misses: list[TranslateBlock] = []
        for b in req.blocks:
            row = cached.get(b.key)
            if not req.force and _usable(row, b, language):
                assert row is not None
                results[b.key] = TranslateResult(
                    key=b.key, status="ok", text=tidy_cjk_spacing(row.text),
                    model=row.model,
                    stale_terminology=row.memory_hash != memory_hash,
                )
            else:
                misses.append(b)

        # In-flight de-duplication: a block another request is already
        # translating is awaited, not sent again. No await between the
        # check and the registration, so two requests cannot both own it.
        owned: list[TranslateBlock] = []
        futures: dict[str, asyncio.Future[_Outcome]] = {}
        joined: dict[str, asyncio.Future[_Outcome]] = {}
        loop = asyncio.get_running_loop()
        for b in misses:
            fkey = (pid, b.key, language, source_hash(b.text))
            existing = self._inflight.get(fkey)
            if existing is not None:
                joined[b.key] = existing
            else:
                fut: asyncio.Future[_Outcome] = loop.create_future()
                self._inflight[fkey] = futures[b.key] = fut
                owned.append(b)

        translated: dict[str, tuple[str, str]] = {}
        try:
            if owned:
                async with self._sem(pid):
                    translated = await self._translate_misses(
                        pid, language, terminology, owned, req.blocks, cached,
                    )
                await self._store(conn, pid, language, memory_hash, owned, translated)
        finally:
            for b in owned:
                fut = futures[b.key]
                if not fut.done():
                    fut.set_result(translated.get(b.key))
                self._inflight.pop((pid, b.key, language, source_hash(b.text)), None)

        for k, f in joined.items():
            outcome = await asyncio.shield(f)
            if outcome is not None:
                translated[k] = outcome

        for b in misses:
            hit = translated.get(b.key)
            row = cached.get(b.key)
            if hit is not None:
                results[b.key] = TranslateResult(
                    key=b.key, status="ok", text=hit[0], model=hit[1],
                )
            elif _usable(row, b, language):
                assert row is not None
                # Ruling K: a forced re-translation that fails keeps showing
                # the cached text, still marked stale.
                results[b.key] = TranslateResult(
                    key=b.key, status="ok", text=tidy_cjk_spacing(row.text),
                    model=row.model,
                    stale_terminology=True,
                )
            else:
                results[b.key] = TranslateResult(key=b.key, status="unavailable")

        return TranslateResponse(results=[results[k] for k in keys])

    @staticmethod
    async def _store(
        conn: aiosqlite.Connection,
        pid: int,
        language: str,
        memory_hash: str,
        owned: list[TranslateBlock],
        translated: dict[str, tuple[str, str]],
    ) -> None:
        """Cache this request's clean translations in one write transaction.

        A failed write is logged and swallowed: the translations are already
        done, so the caller still gets them (they are just not cached)."""
        hits = [(b, translated[b.key]) for b in owned if b.key in translated]
        if not hits:
            return
        try:
            async with write_transaction(conn):
                for b, (text, model) in hits:
                    await put_cached(conn, pid, b.key, language, source_hash(b.text),
                                     memory_hash, text, model)
        except Exception as exc:  # noqa: BLE001 — results still go back
            logger.warning(
                "translation cache write failed: paper=%s keys=%s language=%s error=%s",
                pid, [b.key for b, _ in hits], language,
                f"{type(exc).__name__}: {exc}",
            )

    async def _translate_misses(
        self,
        pid: int,
        language: str,
        terminology: str,
        misses: list[TranslateBlock],
        all_blocks: list[TranslateBlock],
        cached: dict[str, CachedRow],
    ) -> dict[str, tuple[str, str]]:
        """Return ``{key: (text, model)}`` for the misses that translated cleanly.

        Content failures (a block missing or with broken placeholders in a
        reply that arrived) go small alone → flagship alone → unavailable.
        A call-level error means the small tier is down (Ruling J): the batch
        error sends the whole remainder in one flagship call, and a raising
        small retry skips the small retry for every block after it."""
        # A miss's old cached text is for a different source (or is being
        # forced), so it is never used as context.
        miss_keys = {b.key for b in misses}
        known = {k: r.text for k, r in cached.items() if k not in miss_keys}

        def context_for(block: TranslateBlock) -> str:
            i = next(n for n, b in enumerate(all_blocks) if b.key == block.key)
            prev = all_blocks[i - 1].key if i > 0 else _html_predecessor(block.key)
            return known.get(prev, "") if prev else ""

        out: dict[str, tuple[str, str]] = {}
        deadline = asyncio.get_running_loop().time() + self._budget_s

        def accept(blocks: list[TranslateBlock], reply: dict[str, str],
                   model: str) -> dict[str, str]:
            """Keep the clean translations; return ``{key: problem}`` for the rest."""
            problems: dict[str, str] = {}
            for b in blocks:
                raw = reply.get(b.key)
                text = tidy_cjk_spacing(raw) if raw is not None else None
                problem = _problem(b.text, text, language)
                if problem is None and text is not None:
                    out[b.key] = (text, model)
                    known[b.key] = text
                else:
                    problems[b.key] = problem or "missing from reply"
            return problems

        async def call(blocks: list[TranslateBlock], model: str,
                       ) -> tuple[dict[str, str], BaseException | None]:
            return await self._call(language, terminology, context_for(blocks[0]),
                                    blocks, model, deadline)

        reply, err = await call(misses, self._model)
        problems = accept(misses, reply, self._model)
        failed = [b for b in misses if b.key in problems]
        if failed:
            self._log("batch", pid, failed, language, err, self._model, problems)

        if err is not None and failed:
            reply, err = await call(failed, self._flagship)
            problems = accept(failed, reply, self._flagship)
            failed = [b for b in failed if b.key in problems]
            if failed:
                self._log("flagship batch", pid, failed, language, err, self._flagship,
                          problems)
                self._log_unavailable(pid, failed, language)
            return out

        small_down = False
        for b in failed:
            models = (self._flagship,) if small_down else (self._model, self._flagship)
            for model in models:
                single, err = await call([b], model)
                problems = accept([b], single, model)
                if not problems:
                    break
                self._log("retry", pid, [b], language, err, model, problems)
                if err is not None and model == self._model:
                    small_down = True
            else:
                self._log_unavailable(pid, [b], language)
        return out

    async def _call(
        self,
        language: str,
        terminology: str,
        context: str,
        blocks: list[TranslateBlock],
        model: str,
        deadline: float,
    ) -> tuple[dict[str, str], BaseException | None]:
        """One structured call → (first text per key, provider error or None).

        The call is cut off at ``deadline`` (the request's time budget); once
        it has passed no call is made and the blocks fail with a timeout."""
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return {}, TimeoutError(
                f"request time budget ({self._budget_s:g}s) spent before this call")
        try:
            reply = await asyncio.wait_for(self._llm.structured(
                slot=SLOT,
                variables={
                    "language": language,
                    "terminology": terminology,
                    "context": context,
                    "blocks_xml": _blocks_xml(blocks),
                },
                response_model=TranslatedBlocks,
                model=model,
            ), remaining)
        except TimeoutError:
            return {}, TimeoutError(
                f"call cut off by the {self._budget_s:g}s request time budget")
        except Exception as exc:  # noqa: BLE001 — every failure escalates
            return {}, exc
        texts: dict[str, str] = {}
        for item in reply.items:
            # The blocks are sent XML-escaped; a model may copy the entities.
            texts.setdefault(item.key, html.unescape(item.text))
        return texts, None

    @staticmethod
    def _log(
        stage: str,
        pid: int,
        blocks: list[TranslateBlock],
        language: str,
        err: BaseException | None,
        model: str,
        problems: dict[str, str],
    ) -> None:
        if err is not None:
            reason = f"{type(err).__name__}: {err}"
        else:
            reason = "; ".join(f"{b.key}: {problems.get(b.key, '?')}" for b in blocks)
        logger.warning(
            "translation %s failed: paper=%s keys=%s language=%s model=%s error=%s",
            stage, pid, [b.key for b in blocks], language, model, reason,
        )

    @staticmethod
    def _log_unavailable(pid: int, blocks: list[TranslateBlock], language: str) -> None:
        logger.warning(
            "translation unavailable: paper=%s keys=%s language=%s",
            pid, [b.key for b in blocks], language,
        )
