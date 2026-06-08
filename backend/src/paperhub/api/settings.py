"""GET/PATCH /settings — runtime config panel (Plan G / FR-14)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from paperhub import settings_overlay as ov
from paperhub.config import load_settings
from paperhub.db.connection import open_db, write_transaction
from paperhub.settings_registry import (
    PROVIDER_CREDENTIAL_SUGGESTIONS,
    SETTINGS_REGISTRY,
    coerce_value,
    field_by_key,
    is_allowed_credential_key,
)

router = APIRouter(prefix="/settings", tags=["settings"])

_CATEGORY_LABELS = {
    "provider_credentials": "Provider credentials",
    "llm_models": "LLM models",
    "agent_tunables": "Agent tunables",
    "memory": "Memory / recall",
    "external_services": "External services",
    "external_lookup": "External lookup",
    "storage": "Workspace / storage",
    "logging": "Logging",
    "marker": "Marker",
    "slides": "Slide style",
}

# Order categories deterministically for the modal's left-nav.
_CATEGORY_ORDER = [
    "provider_credentials", "llm_models", "agent_tunables", "memory",
    "external_services", "external_lookup", "storage", "logging", "marker", "slides",
]


async def _db_rows(db_path: Path) -> dict[str, str]:
    async with open_db(db_path) as conn, conn.execute(
        "SELECT key, value FROM settings"
    ) as cur:
        return {r[0]: r[1] for r in await cur.fetchall()}


@router.get("")
async def get_settings() -> dict[str, Any]:
    settings = load_settings()
    rows = await _db_rows(settings.db_path)

    cats: dict[str, list[dict[str, Any]]] = {k: [] for k in _CATEGORY_ORDER}

    # Free-form provider credentials: every DB row that is an allowed credential
    # key (and not a structured registry field). Values are NEVER returned.
    for key in sorted(rows):
        if field_by_key(key) is None and is_allowed_credential_key(key):
            cats["provider_credentials"].append(
                {"key": key, "label": key, "type": "secret",
                 "secret": True, "is_set": True, "restart_required": False}
            )

    # Structured fields from the registry.
    for f in SETTINGS_REGISTRY:
        effective = os.environ.get(f.key, f.default)
        item: dict[str, Any] = {
            "key": f.key, "label": f.label, "type": f.type,
            "secret": f.secret, "restart_required": f.restart_required,
            "read_only": f.read_only, "help": f.help,
            "is_default": f.key not in rows,
        }
        if f.choices:
            item["choices"] = list(f.choices)
        if f.min is not None:
            item["min"] = f.min
        if f.max is not None:
            item["max"] = f.max
        if f.secret:
            item["is_set"] = bool(effective)
        else:
            item["value"] = effective
        cats[f.category].append(item)

    return {
        "categories": [
            {
                "key": c,
                "label": _CATEGORY_LABELS[c],
                "free_form": c == "provider_credentials",
                "suggestions": list(PROVIDER_CREDENTIAL_SUGGESTIONS)
                if c == "provider_credentials" else [],
                "fields": cats[c],
            }
            for c in _CATEGORY_ORDER
        ]
    }


@router.patch("")
async def patch_settings(body: dict[str, str | None]) -> dict[str, Any]:
    updated: list[str] = []
    cleared: list[str] = []
    restart: list[str] = []

    # Validate + coerce ALL keys first (reject the whole request on any error).
    to_set: dict[str, str] = {}
    to_clear: list[str] = []
    for key, raw in body.items():
        field = field_by_key(key)
        is_cred = field is None and is_allowed_credential_key(key)
        if field is None and not is_cred:
            raise HTTPException(422, f"Unknown or non-editable setting: {key}")
        if field is not None and field.read_only:
            raise HTTPException(422, f"{key} is read-only.")
        if raw is None or raw.strip() == "":
            to_clear.append(key)
            continue
        if field is not None:
            try:
                to_set[key] = coerce_value(field, raw)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        else:  # credential: opaque non-empty secret
            to_set[key] = raw.strip()

    settings = load_settings()
    async with open_db(settings.db_path) as conn, write_transaction(conn):
        for key, value in to_set.items():
            await conn.execute(
                "INSERT INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=datetime('now')",
                (key, value),
            )
        for key in to_clear:
            await conn.execute("DELETE FROM settings WHERE key = ?", (key,))

    # DB committed — now project onto os.environ and build the response.
    for key, value in to_set.items():
        ov.set_override(key, value)
        updated.append(key)
        f = field_by_key(key)
        if f is not None and f.restart_required:
            restart.append(key)
    for key in to_clear:
        ov.clear_override(key)
        cleared.append(key)

    return {"updated": updated, "cleared": cleared, "restart_required": restart}
