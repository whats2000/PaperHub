import aiosqlite

from paperhub.cli.replay import replay_run


async def test_replay_reconstructs_step_sequence(
    migrated_db: aiosqlite.Connection,
) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute(
        "INSERT INTO runs (session_id, routing_decision_json, status) "
        "VALUES (1, ?, 'ok')",
        ('{"intent":"chitchat","model_tier":"small","confidence":0.9,"reasoning":"x"}',),
    )
    await migrated_db.commit()
    for idx, (agent, tool) in enumerate([("router", "classify"), ("chitchat", "generate")]):
        await migrated_db.execute(
            "INSERT INTO tool_calls (run_id, branch, step_index, agent, tool, "
            "latency_ms, status) VALUES (1, '', ?, ?, ?, 10, 'ok')",
            (idx, agent, tool),
        )
    await migrated_db.commit()

    report = await replay_run(migrated_db, run_id=1)
    assert "router · classify" in report
    assert "chitchat · generate" in report
    assert "intent=chitchat" in report
