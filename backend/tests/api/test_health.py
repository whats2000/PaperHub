"""Smoke tests for the FastAPI app shell."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(tmp_path / "paperhub.db"))
    from paperhub.api.app import create_app

    with TestClient(create_app()) as tc:
        yield tc


def test_health_endpoint_returns_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "app": "paperhub", "schema_version": 2}
