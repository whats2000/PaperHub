"""The paper_qa subagents' view of the conversation (run 892): they gather
evidence for the answering agent, so they need what was just discussed."""
from __future__ import annotations

from paperhub.agents.recent_conversation import (
    RECENT_ANSWER_CHARS,
    recent_conversation,
)


def test_first_message_has_no_conversation() -> None:
    assert recent_conversation([]) == ""
    assert recent_conversation(None) == ""


def test_takes_the_last_exchange_and_strips_citations() -> None:
    history = [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "How do I port GISC to YOLOSeg?"},
        {"role": "assistant",
         "content": "Treat defects as the sparse signal [chunk:86149] (p.1) and the grid "
                    "as background [chunk:82421, 86149]."},
    ]
    block = recent_conversation(history)
    assert "old question" not in block and "old answer" not in block
    assert ("<previous_user_message>How do I port GISC to YOLOSeg?"
            "</previous_user_message>") in block
    assert "Treat defects as the sparse signal and the grid as background." in block
    assert "[chunk:" not in block and "(p.1)" not in block
    assert 'truncated="true"' not in block


def test_bounds_the_previous_answer() -> None:
    long_answer = "x" * (RECENT_ANSWER_CHARS + 500)
    block = recent_conversation([{"role": "user", "content": "q"},
                                 {"role": "assistant", "content": long_answer}])
    assert 'truncated="true"' in block
    assert "x" * RECENT_ANSWER_CHARS in block
    assert "x" * (RECENT_ANSWER_CHARS + 1) not in block


def test_user_turn_without_an_answer_yet() -> None:
    block = recent_conversation([{"role": "user", "content": "only a question"}])
    assert "<previous_user_message>only a question</previous_user_message>" in block
    assert "previous_answer_excerpt" not in block
