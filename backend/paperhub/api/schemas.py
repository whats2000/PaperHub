"""HTTP request/response schemas for the FastAPI surface."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    app: Literal["paperhub"]
    schema_version: int
