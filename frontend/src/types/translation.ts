// Plan H1 bilingual-reading API contract (mirrors backend translation/models.py).

export interface TranslateBlock {
  key: string; // HTML "h:<index>", PDF "p:<first Marker block_id>"
  text: string; // inline content replaced by ⟦n⟧ placeholders
}

export interface TranslateRequest {
  paper_content_id: number;
  language: string;
  force?: boolean;
  blocks: TranslateBlock[]; // 1..5
}

export interface TranslateResult {
  key: string;
  status: "ok" | "unavailable";
  text: string | null;
  model: string | null;
  stale_terminology: boolean;
}

export interface TranslateResponse {
  results: TranslateResult[];
}

export interface TranslateConfig {
  full_paper: boolean;
}

export interface PdfFragment {
  page: number; // 0-based
  bbox: [number, number, number, number]; // PDF points, top-left origin
}

export interface PdfParagraph {
  key: string;
  text: string;
  math: string[]; // math[n] = LaTeX for ⟦n⟧
  fragments: PdfFragment[];
}

export interface ParagraphsResponse {
  status: "ready" | "no_blocks" | "no_inline_math_llm";
  paragraphs: PdfParagraph[];
}
