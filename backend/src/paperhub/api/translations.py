"""Translation REST surface (Plan H1). A batch is a pipeline call, not a chat
turn: no ``runs`` row."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from paperhub.api.deps import get_llm
from paperhub.config import flagship_tier_model, load_settings
from paperhub.db.connection import open_db
from paperhub.translation.models import (
    TranslateConfig,
    TranslateRequest,
    TranslateResponse,
)
from paperhub.translation.service import TranslationService

router = APIRouter(prefix="/translations", tags=["translations"])


def get_translation_service(request: Request) -> TranslationService:
    """``app.state.translation_service`` when set (tests inject a stub), else a
    cached default that is rebuilt when the runtime model slots change (so the
    Settings panel takes effect without a restart, like other model slots)."""
    state = request.app.state
    injected = getattr(state, "translation_service", None)
    if injected is not None:
        return injected  # type: ignore[no-any-return]
    model = load_settings().translate_model
    flagship = flagship_tier_model()
    cached = getattr(state, "_translation_default", None)
    if cached is not None and cached[0] == (model, flagship):
        return cached[1]  # type: ignore[no-any-return]
    svc = TranslationService(get_llm(request), model=model, flagship_model=flagship)
    state._translation_default = ((model, flagship), svc)
    return svc


@router.get("/config", response_model=TranslateConfig)
async def translation_config() -> TranslateConfig:
    return TranslateConfig(full_paper=load_settings().translate_full_paper)


@router.post("", response_model=TranslateResponse)
async def translate(req: TranslateRequest, request: Request) -> TranslateResponse:
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        async with conn.execute(
            "SELECT 1 FROM paper_content WHERE id = ?", (req.paper_content_id,),
        ) as cur:
            if await cur.fetchone() is None:
                raise HTTPException(
                    404, f"paper_content {req.paper_content_id} not found",
                )
        return await get_translation_service(request).translate(conn, req)
