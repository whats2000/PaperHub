import argparse
import asyncio
import json

import aiosqlite

from paperhub.config import load_settings
from paperhub.db.connection import open_db


async def replay_run(conn: aiosqlite.Connection, *, run_id: int) -> str:
    async with conn.execute(
        "SELECT session_id, routing_decision_json, status FROM runs WHERE id = ?",
        (run_id,),
    ) as cur:
        run_row = await cur.fetchone()
    if run_row is None:
        return f"run {run_id} not found"
    session_id, decision_json, status = run_row
    decision = json.loads(decision_json) if decision_json else {}

    async with conn.execute(
        "SELECT branch, step_index, agent, tool, model, status, latency_ms, error "
        "FROM tool_calls WHERE run_id = ? ORDER BY branch, step_index",
        (run_id,),
    ) as cur:
        steps = await cur.fetchall()

    lines: list[str] = [
        f"run {run_id} (session {session_id}, status={status})",
        f"  intent={decision.get('intent','?')} "
        f"tier={decision.get('model_tier','?')} "
        f"conf={decision.get('confidence','?')}",
    ]
    for branch, step_index, agent, tool, model, st, latency_ms, error in steps:
        prefix = f"  [{branch or 'main'}#{step_index}]"
        line = f"{prefix} {agent} · {tool} ({model or '-'}) {latency_ms}ms {st}"
        if error:
            line += f" — {error}"
        lines.append(line)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a PaperHub run from SQLite")
    parser.add_argument("--run-id", type=int, required=True)
    args = parser.parse_args()
    settings = load_settings()

    async def _run() -> None:
        async with open_db(settings.db_path) as conn:
            print(await replay_run(conn, run_id=args.run_id))

    asyncio.run(_run())


if __name__ == "__main__":
    main()
