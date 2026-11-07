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
    # The affiliation itself is a meta line: not translated (QA F1).
    assert [p.key for p in ps] == ["p:/page/0/Text/15"]
    assert ps[0].text == "This occurs because ⟦BR⟧ the model is uncertain."


def test_footnote_like_prose_is_its_own_paragraph() -> None:
    ps = build_paragraphs([
        _b("a", "<p>This occurs because</p>"),
        _b("f", "<p><sup>1</sup>Code is released under the MIT licence</p>"),
        _b("b", "<p>the model is uncertain.</p>", page=1),
    ])
    assert [p.key for p in ps] == ["p:a", "p:f"]
    assert ps[1].math[0] == r"\text{1}"


def test_footnote_like_text_does_not_open_a_merge() -> None:
    ps = build_paragraphs([
        _b("f", "<p><sup>1</sup>Affiliation without a period</p>"),
        _b("n", "<p>Next paragraph starts here.</p>"),
    ])
    assert [p.key for p in ps] == ["p:f", "p:n"]


def _g(i: str, html: str, bbox: tuple[float, float, float, float], page: int = 0,
       t: str = "Text", section: str | None = None) -> BlockAsset:
    return BlockAsset(i, t, page, bbox, html, section)


def test_stacked_front_matter_blocks_stay_apart() -> None:
    """QA B3: paper 89 joined the correspondence line, the dates line and the
    abstract (same column, one under the other, no bridge) with ⟦BR⟧."""
    ps = build_paragraphs([
        _g("/page/0/Text/7", "<p>Received: 20 June 2018; Accepted: 27 July 2018</p>",
           (82, 223, 378, 232)),
        _g("/page/0/Text/8", "<p><b>Abstract:</b> Different from conventional imaging"
           " methods, ghost imaging obtains the image of an object</p>",
           (81, 244, 520, 365)),
        _g("/page/0/Text/11", "<p>With the concept of photon introduced by</p>",
           (74, 448, 520, 664)),
    ])
    assert [p.key for p in ps] == ["p:/page/0/Text/8", "p:/page/0/Text/11"]
    assert "⟦BR⟧" not in ps[0].text


def test_next_column_still_merges() -> None:
    ps = build_paragraphs([
        _g("a", "<p>the model collapses because</p>", (50, 600, 290, 760)),
        _g("b", "<p>its load is unbalanced.</p>", (310, 80, 550, 120)),
    ])
    assert len(ps) == 1 and ps[0].text.endswith("unbalanced.")


def test_equation_bridge_in_same_column_still_merges() -> None:
    ps = build_paragraphs([
        _g("a", "<p>we minimise</p>", (50, 100, 290, 140)),
        _g("e", "<math>L</math>", (50, 145, 290, 160), t="Equation"),
        _g("b", "<p>over all experts.</p>", (50, 165, 290, 200)),
    ])
    assert len(ps) == 1


def test_bylines_affiliations_and_dois_are_skipped() -> None:
    """QA F1: names, affiliations, correspondence and DOI lines are not
    translated (no paragraph, so no hover card)."""
    ps = build_paragraphs([
        _g("/page/0/Text/3", "<p>Yi Tay yitay@google.com</p>", (90, 134, 522, 146)),
        _g("/page/0/Text/4", "<p>Google Research</p>", (89, 148, 161, 159)),
        _g("/page/0/Text/5", "<p>* Correspondence: sshan@mail.shcnc.ac.cn</p>",
           (80, 203, 256, 214)),
        _g("/page/0/Text/6", "<p>doi:10.3390/app8081379</p>", (80, 220, 256, 230)),
        _g("/page/0/Text/13", "<p>Transformer model architectures have garnered"
           " immense interest lately.</p>", (110, 327, 502, 447)),
    ])
    assert [p.key for p in ps] == ["p:/page/0/Text/13"]


def test_reference_list_is_skipped_until_the_next_section() -> None:
    ps = build_paragraphs([
        _g("s1", "<h1>References</h1>", (0, 0, 1, 1), t="SectionHeader",
           section="References"),
        _g("r1", "<li>Ryan Adams and Richard Zemel. Ranking via sinkhorn propagation."
           " arXiv preprint, 2011.</li>", (50, 20, 290, 40), t="ListItem",
           section="References"),
        _g("r2", "<li>Jimmy Ba, Jamie Kiros. Layer normalization. 2016.</li>",
           (50, 50, 290, 70), page=1, t="ListItem"),  # later page: no section
        _g("s2", "<h1>A Appendix</h1>", (0, 80, 1, 90), page=1, t="SectionHeader"),
        _g("x", "<p>We list the hyper-parameters here.</p>", (50, 100, 290, 120), page=1),
    ])
    assert [p.key for p in ps] == ["p:x"]


def test_a_block_in_a_references_section_is_skipped() -> None:
    ps = build_paragraphs([
        _g("r", "<p>Vaswani et al. Attention is all you need. 2017.</p>",
           (50, 20, 290, 40), section="REFERENCES"),
    ])
    assert ps == []
