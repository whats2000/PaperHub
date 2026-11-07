"""Echo detection, front-matter/meta detection and CJK spacing (H1 QA fixes)."""

from paperhub.translation.language import canonical_language, language_code
from paperhub.translation.quality import (
    is_echo,
    is_meta_block,
    looks_untranslated,
    tidy_cjk_spacing,
)

ABSTRACT = (
    "Different from conventional imaging methods, which are based on the "
    "first-order field correlation, ghost imaging (GI) obtains the image of "
    "an object through high-order correlation of light fields ⟦0⟧."
)


# ── language labels ─────────────────────────────────────────────────────────

def test_known_labels_share_one_canonical_key() -> None:
    for label in ("日本語", "Japanese", " japanese ", "ja", "JA-jp", "日文"):
        assert canonical_language(label) == "日本語", label
    for label in ("繁體中文", "zh-TW", "Traditional Chinese", "traditional  chinese",
                  "繁体中文", "zh-Hant", "Chinese (Traditional)"):
        assert canonical_language(label) == "繁體中文", label
    for label in ("简体中文", "簡體中文", "zh-CN", "Simplified Chinese"):
        assert canonical_language(label) == "简体中文", label
    assert canonical_language("english") == "English"
    assert canonical_language("Korean") == "한국어"


def test_free_text_label_keeps_its_own_key() -> None:
    assert canonical_language("  繁體中文（香港）  ") == "繁體中文（香港）"
    assert canonical_language("Pirate   English") == "Pirate English"
    assert canonical_language("Chinese") == "Chinese"  # ambiguous: not guessed


def test_language_code_infers_script_of_free_text() -> None:
    assert language_code("繁體中文（香港）") == "zh"
    assert language_code("日本語") == "ja"
    assert language_code("English") == "en"
    assert language_code("Klingon") is None


# ── echo ────────────────────────────────────────────────────────────────────

def test_verbatim_echo_into_cjk_is_detected() -> None:
    assert is_echo(ABSTRACT, ABSTRACT, "繁體中文")
    assert is_echo(ABSTRACT, ABSTRACT, "日本語")
    assert is_echo(ABSTRACT, ABSTRACT, "繁體中文（香港）")


def test_reworded_english_into_cjk_is_an_echo() -> None:
    out = ABSTRACT.replace("Different from", "Unlike")
    assert is_echo(ABSTRACT, out, "繁體中文")


def test_echo_into_a_latin_target_is_detected() -> None:
    assert is_echo(ABSTRACT, ABSTRACT, "Français")
    assert is_echo(ABSTRACT, ABSTRACT, "Klingon")


def test_real_translation_is_not_an_echo() -> None:
    zh = "不同於基於一階場關聯的傳統成像方法，鬼成像（GI）透過光場的高階關聯取得物體影像 ⟦0⟧。"
    assert not is_echo(ABSTRACT, zh, "繁體中文")
    ja = "従来の撮像法とは異なり、ゴーストイメージング（GI）は高次相関により像を得る ⟦0⟧。"
    assert not is_echo(ABSTRACT, ja, "日本語")


def test_english_target_never_echoes() -> None:
    assert not is_echo(ABSTRACT, ABSTRACT, "English")


def test_untranslatable_blocks_are_not_echoes() -> None:
    names = "Shensheng Han *, Hong Yu, Xia Shen, Honglin Liu, Wenlin Gong and Zhentao Liu"
    assert not is_echo(names, names, "繁體中文")
    doi = "doi:10.3390/app8081379"
    assert not is_echo(doi, doi, "繁體中文")
    math = "⟦0⟧ = ⟦1⟧ + ⟦2⟧"
    assert not is_echo(math, math, "繁體中文")
    short = "Transformer"
    assert not is_echo(short, short, "繁體中文")
    already = "這段文字本來就是中文，所以模型原樣輸出也是正確的結果。"
    assert not is_echo(already, already, "繁體中文")


def test_looks_untranslated_needs_no_source() -> None:
    assert looks_untranslated(ABSTRACT, "繁體中文")
    assert not looks_untranslated(ABSTRACT, "English")
    assert not looks_untranslated("鬼成像透過光場的高階關聯取得物體影像。", "繁體中文")
    assert not looks_untranslated("Shensheng Han, Hong Yu and Xia Shen", "繁體中文")


# ── meta blocks (bylines, affiliations, DOI) ────────────────────────────────

def test_meta_blocks_are_detected() -> None:
    for text in (
        "Shensheng Han *, Hong Yu, Xia Shen, Honglin Liu, Wenlin Gong and Zhentao Liu",
        "Shanghai Institute of Optics & Fine Mechanics, Chinese Academy of Sciences,"
        " Shanghai 201800, China; yuhong@siom.ac.cn (H.Y.); shenxia@siom.ac.cn (X.S.)",
        "* Correspondence: sshan@mail.shcnc.ac.cn",
        "Yi Tay yitay@google.com",
        "Google Research, Brain team",
        "doi:10.3390/app8081379",
        "https://doi.org/10.1038/s41586-021-03819-2",
        "⟦0⟧OATML, Department of Computer Science, University of Oxford ⟦1⟧Department of Statistics",
    ):
        assert is_meta_block(text), text


def test_prose_and_keywords_are_not_meta() -> None:
    for text in (
        ABSTRACT,
        "Keywords: Deep Learning, Natural Language Processing, Transformer Models",
        "We thank Google Research for compute.",
        "這是一段中文。",
        "⟦0⟧ = ⟦1⟧",
    ):
        assert not is_meta_block(text), text


# ── CJK spacing ─────────────────────────────────────────────────────────────

def test_spaces_inside_fullwidth_punctuation_are_removed() -> None:
    assert tidy_cjk_spacing("任務（ 例如語言建模與機器翻譯 ）中") == "任務（例如語言建模與機器翻譯）中"
    assert tidy_cjk_spacing("結果 ，如下 。") == "結果，如下。"
    assert tidy_cjk_spacing("「 注意力 」") == "「注意力」"


def test_placeholders_and_latin_spacing_survive() -> None:
    assert tidy_cjk_spacing("使用 ⟦0⟧ 個參數 （ ⟦1⟧ ）") == "使用 ⟦0⟧ 個參數（⟦1⟧）"
    assert tidy_cjk_spacing("前段。 ⟦BR⟧ 後段") == "前段。⟦BR⟧ 後段"
    assert tidy_cjk_spacing("GPT-2 model (see above)") == "GPT-2 model (see above)"
