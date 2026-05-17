-- Migration 0002: record which extraction tier produced each paper's primary artifact
-- Tier 1 = 'latex' (lossless, arxiv-latex-mcp), Tier 2 = 'marker' (Phase B), Tier 3 = 'raw' (lossy fallback)
--
-- papers.pdf_path now holds the PRIMARY ARTIFACT path, which may be:
--   .tex  — Tier 1 LaTeX source (workspace_root/papers/<id>/source.tex)
--   .md   — Tier 3 raw HTML→Markdown fallback (workspace_root/papers/<id>/fallback.md)
--   .pdf  — future Tier 2 / PDF-based path
-- The column name is kept as-is to avoid breaking the existing model + tests.
--
-- papers.notes_md holds import-time annotations, e.g. 'low_fidelity_extraction'
-- to signal that downstream consumers (slide pipeline in Phase B) should refuse
-- this artifact for slides.

ALTER TABLE papers ADD COLUMN extraction_tier TEXT
    CHECK(extraction_tier IN ('latex','marker','raw'));

-- Backfill: any pre-migration rows are from the pre-ladder Phase A flow (raw markdown)
UPDATE papers SET extraction_tier = 'raw' WHERE extraction_tier IS NULL;

ALTER TABLE papers ADD COLUMN notes_md TEXT;
-- Import-time annotations (e.g. 'low_fidelity_extraction' for Tier 3 artifacts).
-- NULL = no annotation (Tier 1 / Tier 2 artifacts).
