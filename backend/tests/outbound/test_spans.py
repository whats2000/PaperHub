"""Verbatim-span windows: CJK mixed text, Unicode normalisation, memoisation."""
from __future__ import annotations

import unicodedata

import pytest

from paperhub.outbound import context, spans
from paperhub.outbound.context import PrivateItem, add_private, begin_turn, echoes_private

JA = "NMC811セルを45度で300サイクル運転した後、容量が18%低下した"
ZH = "我们在 2025 年用 GPT4 微调了客服模型，准确率提升到 93.5% 之后上线"


def test_mixed_japanese_echo_is_caught() -> None:
    assert spans.shares_span(JA, [f"社内報告：{JA}。以上"])
    assert spans.shares_span(JA.replace("45", "４５"), [JA])  # full-width digits


def test_mixed_chinese_with_digits_and_latin_is_caught() -> None:
    assert spans.shares_span("请问 GPT4 微调了客服模型，准确率提升到 93.5% 之后", [ZH])


def test_short_cjk_overlap_and_latin_words_do_not_match() -> None:
    assert not spans.shares_span("45度で300サイクル", [JA])  # < 12 chars of stream
    assert not spans.shares_span("semiconductorwafer defects", ["semiconductorwafer ホ"])


def test_nfkc_and_casefold() -> None:
    assert spans.normalise("ＮＭＣ８１１") == "nmc811"
    assert spans.normalise("ﾃｽﾄ") == "テスト"
    private = "the quick brown fox jumps over the lazy dog today"
    assert spans.shares_span("THE QUICK BROWN FOX JUMPS OVER THE LAZY", [private])
    assert spans.shares_span("the quick brown fox jumps over the lazy",
                             [unicodedata.normalize("NFD", "the quick brown fox jumps over the lazy dog café")])
    assert unicodedata.normalize("NFD", "café") != "café"
    assert spans.normalise(unicodedata.normalize("NFD", "café")) == "café"
    assert spans.shares_span("ｔｈｅ ｑｕｉｃｋ ｂｒｏｗｎ ｆｏｘ ｊｕｍｐｓ ｏｖｅｒ ｔｈｅ ｌａｚｙ", [private])


def test_guard_windows_are_built_once_per_item(monkeypatch: pytest.MonkeyPatch) -> None:
    begin_turn()
    calls: list[str] = []
    real = context.windows
    monkeypatch.setattr(context, "windows", lambda t: (calls.append(t), real(t))[1])
    add_private(PrivateItem("document", "1", "t", "one two three four five six seven eight nine"))
    add_private(PrivateItem("document", "2", "t", "alpha beta gamma delta epsilon zeta eta theta"))
    assert len(calls) == 2  # each item once, at add time
    assert echoes_private("alpha beta gamma delta epsilon zeta eta theta")
    assert echoes_private("two three four five six seven eight nine")
    assert len(calls) == 4  # only the two queries; no rebuild of the items


def test_guarded_title_needs_three_words_and_twelve_characters() -> None:
    assert spans.guarded_title("Sparse Routing for Efficient Sequence Models") == (
        "sparse routing for efficient sequence models")
    assert spans.guarded_title("Yield review") is None          # 2 words
    assert spans.guarded_title("A B C D") is None               # < 12 characters
    assert spans.guarded_title("白鷺プロジェクト 社内報告") == "白鷺プロジェクト 社内報告"
    assert spans.guarded_title("社内報告") is None               # < 12 CJK characters


def test_contains_phrase_is_word_bounded_and_normalised() -> None:
    t = "sparse routing for efficient sequence models"
    assert spans.contains_phrase("SPARSE-routing for efficient sequence_models arxiv", t)
    assert spans.contains_phrase("about Sparse Routing for Efficient Sequence Models's results", t)
    assert not spans.contains_phrase("sparse routing for efficient sequence modelsx", t)
    assert not spans.contains_phrase("sparse routing for efficient sequence", t)
    assert spans.contains_phrase("白鷺プロジェクト社内報告 NMC811", "白鷺プロジェクト 社内報告")


def test_contains_title_catches_reordered_title_words_only_when_asked() -> None:
    """Sweep 'title': the review rewrote a passage into the private title's
    words in another order ("video captioning gated memory replay"). Any
    order is for the review's rewrite only; a query needs the phrase."""
    t = spans.guarded_title("Gated Memory Replay for Streaming Video Captioning")
    assert t is not None
    reordered = "video captioning gated memory replay streaming"
    assert spans.contains_title(reordered, t, any_order=True)
    assert not spans.contains_title(reordered, t)
    # 6 content words: all but one still name the title.
    assert spans.contains_title("video captioning gated memory replay compute efficiency", t,
                                any_order=True)
    # 4 of 6: a topic, not the title.
    assert not spans.contains_title("video captioning memory replay efficiency", t,
                                    any_order=True)


def test_any_order_needs_four_content_words() -> None:
    five = spans.guarded_title("Sparse Routing for Efficient Sequence Models")
    assert five is not None
    # A user's query sharing 4 of 5 title words is a topic: phrase only.
    assert not spans.contains_title("efficient sparse sequence models", five)
    # The review's rewrite with all but one of 5+ words names the title.
    assert spans.contains_title("efficient sparse sequence models", five, any_order=True)
    assert not spans.contains_title("efficient sequence models", five, any_order=True)
    orchid = spans.guarded_title("Orchid NPU tape-out status")  # 5 content words
    assert orchid is not None
    assert spans.contains_title("npu tape out status softmax timing", orchid, any_order=True)
    three = spans.guarded_title("Deep Learning Survey")
    assert three is not None
    assert not spans.contains_title("survey of deep learning for remote sensing", three,
                                    any_order=True)
    assert spans.contains_title("a deep learning survey", three)
