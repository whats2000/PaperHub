"""Contract tests for the bilingual-reading API models (Plan H1 Task 1)."""

import pytest
from pydantic import ValidationError

from paperhub.translation.models import (
    ParagraphsResponse,
    PdfFragment,
    PdfParagraph,
    TranslateBlock,
    TranslateConfig,
    TranslateRequest,
    TranslateResponse,
    TranslateResult,
)


def _blocks(n: int) -> list[TranslateBlock]:
    return [TranslateBlock(key=f"h:{i}", text=f"t{i}") for i in range(n)]


@pytest.mark.parametrize("n", [0, 6])
def test_translate_request_rejects_out_of_range_blocks(n: int) -> None:
    with pytest.raises(ValidationError):
        TranslateRequest(paper_content_id=1, language="zh-TW", blocks=_blocks(n))


@pytest.mark.parametrize("n", [1, 5])
def test_translate_request_accepts_1_to_5_blocks(n: int) -> None:
    req = TranslateRequest(paper_content_id=1, language="zh-TW", blocks=_blocks(n))
    assert req.force is False
    assert len(req.blocks) == n


def test_translate_result_status_is_constrained() -> None:
    ok = TranslateResult(key="h:0", status="ok", text="x", model="m")
    assert ok.stale_terminology is False
    with pytest.raises(ValidationError):
        TranslateResult(key="h:0", status="bogus", text=None, model=None)  # type: ignore[arg-type]


def test_response_and_pdf_models_roundtrip() -> None:
    assert TranslateResponse(results=[]).results == []
    assert TranslateConfig(full_paper=True).full_paper is True
    para = PdfParagraph(
        key="p:/page/0/Text/1",
        text="a ⟦0⟧ b",
        math=["x^2"],
        fragments=[PdfFragment(page=0, bbox=(0.0, 1.0, 2.0, 3.0))],
    )
    resp = ParagraphsResponse(status="ready", paragraphs=[para])
    assert resp.paragraphs[0].fragments[0].bbox == (0.0, 1.0, 2.0, 3.0)
    with pytest.raises(ValidationError):
        ParagraphsResponse(status="nope", paragraphs=[])  # type: ignore[arg-type]


def test_translate_request_rejects_duplicate_keys() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        TranslateRequest(paper_content_id=1, language="ja", blocks=[
            TranslateBlock(key="h:0", text="a"), TranslateBlock(key="h:0", text="b"),
        ])


@pytest.mark.parametrize(("language", "key", "text"), [
    ("  ", "h:0", "a"), ("ja", " ", "a"), ("ja", "h:0", " \n"),
])
def test_translate_request_rejects_blank_fields(language: str, key: str, text: str) -> None:
    with pytest.raises(ValidationError):
        TranslateRequest(paper_content_id=1, language=language,
                         blocks=[TranslateBlock(key=key, text=text)])
