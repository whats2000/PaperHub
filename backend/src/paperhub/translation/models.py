"""API contract for bilingual reading (Plan H1).

One translation endpoint serves both the HTML and PDF views. Block text
carries ``⟦n⟧`` placeholders for protected inline content (math, anchors,
inline code); ``⟦BR⟧`` marks a page/column break inside one PDF paragraph.
"""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class TranslateBlock(BaseModel):
    key: str  # HTML "h:<index>", PDF "p:<first Marker block_id>"
    text: str


class TranslateRequest(BaseModel):
    paper_content_id: int
    language: str
    force: bool = False
    blocks: list[TranslateBlock] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def _check_blocks(self) -> Self:
        """Blank fields and repeated keys are client bugs (a repeated key
        would also make in-flight de-duplication wait on itself)."""
        if not self.language.strip():
            raise ValueError("language must not be blank")
        keys = [b.key for b in self.blocks]
        if any(not k.strip() for k in keys) or any(not b.text.strip() for b in self.blocks):
            raise ValueError("block key and text must not be blank")
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate block keys")
        return self


class TranslateResult(BaseModel):
    key: str
    status: Literal["ok", "unavailable"]
    text: str | None = None
    model: str | None = None
    stale_terminology: bool = False


class TranslateResponse(BaseModel):
    results: list[TranslateResult]


class TranslateConfig(BaseModel):
    full_paper: bool


class PdfFragment(BaseModel):
    page: int  # 0-based
    bbox: tuple[float, float, float, float]  # x0,y0,x1,y1 PDF points, top-left origin


class PdfParagraph(BaseModel):
    key: str
    text: str
    math: list[str]  # math[n] = LaTeX for ⟦n⟧
    fragments: list[PdfFragment]


class ParagraphsResponse(BaseModel):
    status: Literal["ready", "no_blocks", "no_inline_math_llm"]
    paragraphs: list[PdfParagraph]


class TranslatedItem(BaseModel):
    key: str
    text: str


class TranslatedBlocks(BaseModel):
    """LLM response for ``translate_blocks/v1``: one item per input block key."""

    items: list[TranslatedItem]
