from paperhub.pipelines.slide_pipeline.title_meta import (
    build_title_metadata,
    latex_escape,
)


def test_latex_escape_specials():
    assert latex_escape("A & B_C 50%") == r"A \& B\_C 50\%"
    assert latex_escape("x#1 $y") == r"x\#1 \$y"


def _paper(**kw):
    base = {"title": "T", "authors": [], "year": None, "arxiv_id": None, "kind": "arxiv"}
    base.update(kw)
    return base


def test_single_paper_uses_paper_title_authors_arxiv():
    meta = build_title_metadata(
        [_paper(title="Attention Is All You Need",
                authors=["Ashish Vaswani", "Noam Shazeer", "Niki Parmar", "Jakob Uszkoreit"],
                year=2017, arxiv_id="1706.03762")],
        talk_title="A Talk on Transformers",
    )
    assert meta.title == "Attention Is All You Need"
    assert meta.author == "Vaswani, Shazeer, Parmar, et al."
    assert "arXiv:1706.03762" in meta.date and "2017" in meta.date


def test_single_author_no_et_al():
    meta = build_title_metadata(
        [_paper(title="P", authors=["Jane Roe"], year=2020, arxiv_id=None, kind="pdf_upload")],
        talk_title="X",
    )
    assert meta.author == "Roe"
    assert meta.date == "2020"


def test_multi_paper_uses_talk_title_and_lists_sources():
    meta = build_title_metadata(
        [_paper(title="I-JEPA", authors=["Mahmoud Assran"], year=2023, arxiv_id="2301.08243"),
         _paper(title="V-JEPA", authors=["Adrien Bardes"], year=2024, arxiv_id="2404.08471")],
        talk_title="Comparing JEPA Models",
    )
    assert meta.title == "Comparing JEPA Models"
    assert "Assran" in meta.author and "Bardes" in meta.author


def test_escaping_applied_to_title_and_author():
    meta = build_title_metadata(
        [_paper(title="Tom & Jerry", authors=["A_B Smith"], year=2021, arxiv_id="1234.5678")],
        talk_title="T",
    )
    assert meta.title == r"Tom \& Jerry"
    assert meta.author == r"Smith"
