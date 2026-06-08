import os
from pathlib import Path

import aiosqlite
import pytest

from paperhub import settings_overlay as ov
from paperhub.app import apply_settings_overlay_at_boot
from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio


async def test_boot_overlay_applies_db_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES ('PAPERHUB_LOG_LEVEL', 'WARNING')"
        )
        await conn.commit()
    monkeypatch.delenv("PAPERHUB_LOG_LEVEL", raising=False)
    ov.reset_for_tests()
    async with aiosqlite.connect(db_path) as conn:
        await apply_settings_overlay_at_boot(conn)
    assert os.environ["PAPERHUB_LOG_LEVEL"] == "WARNING"
    ov.clear_override("PAPERHUB_LOG_LEVEL")
    ov.reset_for_tests()
