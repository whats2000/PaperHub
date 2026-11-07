from paperhub.pipelines.paper_asset import BlockAsset
from paperhub.translation.pdf_paragraphs import build_paragraphs


def _b(i: str, html: str, t: str = "Text", page: int = 0) -> BlockAsset:
    return BlockAsset(i, t, page, (0.0, float(page), 10.0, 20.0), html, None)


def test_column_break_merges() -> None:
    ps = build_paragraphs([
        _b("a", "<p>so the model</p>"),
        _b("b", "<p>collapses quickly.</p>", page=1),
    ])
    assert len(ps) == 1
    assert ps[0].key == "p:a"
    assert ps[0].text == "so the model ⟦BR⟧ collapses quickly."
    assert [f.page for f in ps[0].fragments] == [0, 1]


def test_sentence_end_does_not_merge() -> None:
    ps = build_paragraphs([_b("a", "<p>One.</p>"), _b("b", "<p>Two</p>")])
    assert [p.key for p in ps] == ["p:a", "p:b"]


def test_inline_math_and_refs_become_placeholders_in_order() -> None:
    ps = build_paragraphs([
        _b("a", '<p>Let <math display="inline">x^2</math> and <math>y</math> '
                'hold <a href="#p1">[3]</a>.</p>'),
    ])
    assert ps[0].text == "Let ⟦0⟧ and ⟦1⟧ hold ⟦2⟧."
    assert ps[0].math == ["x^2", "y", r"\text{[3]}"]


def test_merged_placeholders_renumber() -> None:
    ps = build_paragraphs([
        _b("a", "<p>take <math>a</math> where</p>"),
        _b("e", "<math>E=mc^2</math>", t="Equation"),
        _b("b", "<p>with <math>b</math> small.</p>"),
    ])
    assert len(ps) == 1
    assert ps[0].text == "take ⟦0⟧ where ⟦BR⟧ with ⟦1⟧ small."
    assert ps[0].math == ["a", "b"]


def test_ref_marker_after_period_ends_sentence() -> None:
    ps = build_paragraphs([_b("a", "<p>Done.<sup>1</sup></p>"), _b("b", "<p>Next.</p>")])
    assert len(ps) == 2


def test_chrome_and_non_text_excluded() -> None:
    ps = build_paragraphs([
        _b("h", "Header", t="PageHeader"), _b("f", "3", t="PageFooter"),
        _b("s", "Intro", t="SectionHeader"), _b("p", "pic", t="Picture"),
        _b("t", "tbl", t="Table"), _b("x", "<p>Body.</p>"),
    ])
    assert [p.key for p in ps] == ["p:x"]


def test_zero_area_bbox_emits_no_fragment() -> None:
    nogeo = BlockAsset("a", "Text", 0, (0.0, 0.0, 0.0, 0.0), "<p>the model</p>", None)
    ps = build_paragraphs([nogeo, _b("b", "<p>collapses.</p>", page=1), nogeo.__class__(
        "c", "Text", 0, (0.0, 0.0, 0.0, 0.0), "<p>Alone.</p>", None)])
    assert len(ps) == 2
    assert [f.page for f in ps[0].fragments] == [1]
    assert ps[1].fragments == []


def test_ref_latex_escaped() -> None:
    ps = build_paragraphs([_b("a", '<p>See <a href="#x">Smith &amp; Lee_1 {a}</a>.</p>')])
    assert ps[0].math == [r"\text{Smith \& Lee\_1 \{a\}}"]


def test_trailing_inline_math_runs_on() -> None:
    ps = build_paragraphs([_b("a", "<p>we set <math>k</math></p>"), _b("b", "<p>to one.</p>")])
    assert len(ps) == 1


def test_cjk_and_colon_semicolon_end() -> None:
    for end in ("。", "？", "：", ":", ";"):
        ps = build_paragraphs([_b("a", f"<p>文字{end}</p>"), _b("b", "<p>下一段</p>")])
        assert len(ps) == 2, end


def test_ref_after_nonterminal_text_merges() -> None:
    ps = build_paragraphs([
        _b("a", '<p>as shown in <a href="#r">[3]</a></p>'), _b("b", "<p>prior work.</p>")])
    assert len(ps) == 1


def test_caption_without_period_does_not_merge() -> None:
    ps = build_paragraphs([_b("c", "<p>Figure 1 overview</p>", t="Caption"),
                           _b("b", "<p>Next paragraph.</p>")])
    assert len(ps) == 2


def test_section_header_is_hard_break() -> None:
    ps = build_paragraphs([_b("a", "<p>the model</p>"),
                           _b("s", "Method", t="SectionHeader"),
                           _b("b", "<p>We start.</p>")])
    assert len(ps) == 2


def test_period_then_closing_paren_ends_sentence() -> None:
    ps = build_paragraphs([_b("a", "<p>(see above.)</p>"), _b("b", "<p>Next</p>")])
    assert len(ps) == 2


def test_br_does_not_glue_words() -> None:
    ps = build_paragraphs([_b("a", "<p>one<br/>two</p>")])
    assert ps[0].text == "one two"


def test_footnote_like_text_bridges_an_open_paragraph() -> None:
    """Marker types an affiliation footnote as Text; it starts with a reference
    marker. It stays its own paragraph and never joins (or opens) a merge, so
    the open paragraph still continues past it (Nature paper, p:/page/0/Text/15)."""
    ps = build_paragraphs([
        _b("/page/0/Text/15", "<p>This occurs because</p>"),
        _b("/page/0/Text/16",
           "<p><sup>1</sup>OATML, Department of Computer Science, University of"
           " Oxford <sup>2</sup>Department of Statistics</p>"),
        _b("/page/1/Text/2", "<p>the model is uncertain.</p>", page=1),
    ])
    assert [p.key for p in ps] == ["p:/page/0/Text/15", "p:/page/0/Text/16"]
    assert ps[0].text == "This occurs because ⟦BR⟧ the model is uncertain."
    assert ps[1].text.startswith("⟦0⟧OATML") or ps[1].text.startswith("⟦0⟧ OATML")
    assert ps[1].math[0] == r"\text{1}"


def test_footnote_like_text_does_not_open_a_merge() -> None:
    ps = build_paragraphs([
        _b("f", "<p><sup>1</sup>Affiliation without a period</p>"),
        _b("n", "<p>Next paragraph starts here.</p>"),
    ])
    assert [p.key for p in ps] == ["p:f", "p:n"]
