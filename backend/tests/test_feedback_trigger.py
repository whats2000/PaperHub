import pytest

from paperhub.feedback.models import REASONS, ReviewVerdict
from paperhub.feedback.trigger import DEFAULT_KEYWORDS, keywords_from_env, matches_trigger

EXPECTED = ["不對", "錯了", "不是這個", "我是問", "答非所問", "wrong", "that's not",
            "not what i asked", "incorrect", "違う", "間違", "そうじゃない", "違います", "ちがう"]


def test_default_keywords_cover_zh_en_ja():
    assert DEFAULT_KEYWORDS == EXPECTED


@pytest.mark.parametrize("kw", EXPECTED)
def test_each_default_keyword_matches(kw):
    assert matches_trigger(f"嗯… {kw} 吧", DEFAULT_KEYWORDS)


@pytest.mark.parametrize("msg", ["WRONG, I asked about the decoder", "That's Not it",
                                 "Not What I Asked", "InCorrect"])
def test_matching_is_case_insensitive(msg):
    assert matches_trigger(msg, DEFAULT_KEYWORDS)


def test_keyword_in_keyword_list_is_case_insensitive_too():
    assert matches_trigger("this is wrong", ["WRONG"])


@pytest.mark.parametrize("msg", ["", "   ", "\n"])
def test_empty_message_never_matches(msg):
    assert not matches_trigger(msg, DEFAULT_KEYWORDS)


def test_blank_keywords_never_match():
    assert not matches_trigger("anything", ["", "  "])


def test_plain_message_does_not_match():
    assert not matches_trigger("Summarize section 3 please", DEFAULT_KEYWORDS)


def test_env_override_replaces_the_list(monkeypatch):
    monkeypatch.setenv("PAPERHUB_FEEDBACK_KEYWORDS", " nope , 唔係 ,,")
    kws = keywords_from_env()
    assert kws == ["nope", "唔係"]
    assert matches_trigger("Nope, the other one", kws)
    assert not matches_trigger("wrong", kws)


@pytest.mark.parametrize("value", [None, "", " , "])
def test_env_unset_or_blank_falls_back_to_defaults(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("PAPERHUB_FEEDBACK_KEYWORDS", raising=False)
    else:
        monkeypatch.setenv("PAPERHUB_FEEDBACK_KEYWORDS", value)
    assert keywords_from_env() == DEFAULT_KEYWORDS


def test_defaults_are_not_aliased():
    keywords_from_env().append("x")
    assert "x" not in DEFAULT_KEYWORDS


def test_review_verdict_filters_unknown_reasons():
    v = ReviewVerdict.model_validate(
        {"verdict": "confirmed", "reasons": ["wrong_source", "bogus", "wrong_source"],
         "doubt": "The answer covers paper 1, not paper 2."})
    assert v.reasons == ["wrong_source"]
    assert set(v.reasons) <= set(REASONS)


def test_review_verdict_rejects_unknown_verdict():
    with pytest.raises(ValueError):
        ReviewVerdict.model_validate({"verdict": "maybe", "reasons": [], "doubt": ""})


@pytest.mark.parametrize("msg", ["違います、英独は 28.4 です", "いや、ちがうよ"])
def test_polite_and_kana_japanese_corrections_match(msg):
    assert matches_trigger(msg, DEFAULT_KEYWORDS)


def test_japanese_difference_question_does_not_match():
    assert not matches_trigger("AとBの違いは何ですか", DEFAULT_KEYWORDS)
