"""H4 Task 14c fix 2: the language of a tool's direct answer."""
from __future__ import annotations

import pytest

from paperhub.agents.core.language import direct_answer_language


@pytest.mark.parametrize(("message", "ui", "expected"), [
    ("我的文獻庫裡有幾篇論文？", "en", "Traditional Chinese"),
    ("我的文献库里有几篇论文？", "en", "Simplified Chinese"),
    ("早安！", "zh-TW", "Traditional Chinese"),       # no script-exclusive char
    ("早安！", "zh-CN", "Simplified Chinese"),
    ("早安！", "en", "Chinese, in the script of the user's message"),
    ("ライブラリに論文は何本？", "en", "Japanese"),
    ("내 논문은 몇 편이야?", "en", "Korean"),
    ("How many papers do I have?", "zh-TW", "the language of the user's message"),
    ("2025?", "ja", "Japanese"),                       # no letters: UI language
    ("2025?", None, "English"),
    # Han-only text with a Japanese UI is Japanese (kanji overlap Traditional).
    ("論文数", "ja", "Japanese"),
    # A stray CJK term or title inside an English sentence stays English.
    ("How many 論文 do I have", "en", "the language of the user's message"),
    ("Summarise the paper 「深度學習的未來」 for me please", "en",
     "the language of the user's message"),
    # Han dominates a few Latin terms: Chinese.
    ("這篇 Transformer 論文有幾個 layer？", "en", "Traditional Chinese"),
    ("早安，我的論文庫有幾篇是 2025 年的？", "en", "Traditional Chinese"),
])
def test_direct_answer_language(message: str, ui: str | None, expected: str) -> None:
    assert direct_answer_language(message, ui) == expected
