import json
from collections.abc import AsyncIterator

from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app


async def _consume_sse(stream: AsyncIterator[bytes]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    buf = ""
    async for chunk in stream:
        # Normalise CRLF → LF so the block-splitter works regardless of
        # whether sse_starlette uses \r\n or \n line endings.
        buf += chunk.decode("utf-8").replace("\r\n", "\n")
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            event_type = ""
            data = ""
            for line in block.splitlines():
                if line.startswith("event: "):
                    event_type = line[len("event: "):]
                elif line.startswith("data: "):
                    data = line[len("data: "):]
            if event_type:
                events.append((event_type, json.loads(data) if data else {}))
    return events


async def test_chat_sse_chitchat_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_ROUTER_MOCK",
                       '{"intent":"chitchat","model_tier":"small",'
                       '"confidence":0.9,"reasoning":"greeting"}')
    monkeypatch.setenv("PAPERHUB_CHITCHAT_MOCK", "Hello there!")
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        # Lifespan runs schema migration. Issue request.
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "hi"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    types = [t for t, _ in events]
    assert "routing_decision" in types
    assert types.count("tool_step") >= 2  # router + chitchat
    assert "final" in types
    final_payload = next(d for t, d in events if t == "final")
    assert final_payload["content"] == "Hello there!"
