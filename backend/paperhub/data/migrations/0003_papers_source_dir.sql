-- Migration 0003: Tier 1 imports record the unpacked e-print directory.
--
-- pdf_path now points at the primary artifact (source/<main>.tex for Tier 1
-- arXiv, .md file for Tier 3); source_dir_path is the dedicated directory
-- path for Tier 1 unpacked e-print archives (figures + bib + sty + .tex).
--
-- Tier 2 (Marker, Phase B): source_dir_path will be NULL (Marker produces .md).
-- Tier 3 (raw markdown): source_dir_path will be NULL.
-- Tier 1 (LaTeX, SRS §1.1): source_dir_path = "papers/<arxiv_id>/source" (relative to workspace_root).

ALTER TABLE papers ADD COLUMN source_dir_path TEXT;

-- Backfill: existing Tier-1 rows (from commit f08a1f4) had only the flattened
-- .tex saved at workspace/papers/<id>/source.tex — they don't have an unpacked
-- source dir. Leave source_dir_path NULL for those rows.
-- (No UPDATE needed; SQLite NULL is the default for new columns.)
