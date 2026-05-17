from importlib.resources import files

import aiosqlite


async def apply_schema(conn: aiosqlite.Connection) -> None:
    sql = (files("paperhub.db") / "schema.sql").read_text(encoding="utf-8")
    await conn.executescript(sql)
    await conn.commit()
