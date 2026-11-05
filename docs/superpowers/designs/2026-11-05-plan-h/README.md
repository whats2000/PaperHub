# Plan H design prototype

This is the signed-off interactive design for Plan H (2026-11-05). It is the **visual base for the H1, H2 and H3 implementation plans**: match its layout, states and motion. The settled decisions and rules behind it are in SRS §III-10.

- **View it:** <https://claude.ai/artifact/VT394HjwSdhWXk8FZW1Kww> is a private artifact; ask the owner for access. It covers three areas:
  - **H1 bilingual reading:** HTML papers plus the PDF hover/pin translation card, the translate pill and the language menu.
  - **H3 Ideas page:** graph and kanban views, the idea drawer, problems, lifecycle and dismissal.
  - **H2 Feedback page:** the confirmation queue, stage analysis, ideation feedback and export.
- **The prototype, not the production code.** It is one hand-built HTML page that reuses the app's real tokens from `frontend/src/index.css`, lucide icons and the popover/tooltip styles. The production UI is built from the real React components.

## Files

| File | What it is |
| --- | --- |
| `plan_h_template.html` | The prototype source. All UI, data and interactions live here. The `__PAGES__` and `__PARAS__` placeholders receive the PDF page images and paragraph boxes. |
| `build_prototype.py` | Fills the template from a real PDF, its Marker output and a translations file, producing a self-contained HTML file. |
| `make_artifact.py` | Converts the built file into a page that can be published as an artifact. It removes the document skeleton, loads Geist from Google Fonts, and inlines the KaTeX CSS and fonts. |

The PDF, the Marker JSON and the translations file are **not committed**, because they contain a third-party paper's pages and text. The demo used the Nature article "AI models collapse when trained on recursively generated data" (pages 2–3) from a local workspace upload. To rebuild with any PDF, first run Marker on it, then:

```powershell
uv --directory backend run python docs/superpowers/designs/2026-11-05-plan-h/build_prototype.py `
  <paper.pdf> <marker_pages.json> <translations.json> <out.html> docs/superpowers/designs/2026-11-05-plan-h/plan_h_template.html
```
