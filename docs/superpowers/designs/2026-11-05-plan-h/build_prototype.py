"""Plan H interactive design prototype (throwaway, design review only).

Builds ONE self-contained HTML page that mimics PaperHub's UI (shadcn neutral
tokens from frontend/src/index.css, Geist, lucide icons, popover/tooltip
patterns) and demonstrates every Plan H surface:
  H1 bilingual reading (HTML inline + PDF floating card + pill + language menu
     + settings), H2 feedback (thumbs, reasons popover, private upload,
     Feedback page + export), H3 ideation (Ideas page graph/cards, idea detail,
     domain tag editing, problem mode, open-chat from idea).
PDF pages / Marker paragraph boxes / two formula paragraphs' translations are
real; everything else is illustrative data.
"""
import base64
import html as H
import json
import re
import sys
from pathlib import Path

import fitz

pdf_path, marker_json, tr_json, out_path = (Path(a) for a in sys.argv[1:5])
PAGES = [1, 2]

# ---------------------------------------------------------------- PDF data
doc = fitz.open(pdf_path)
pages = []
for pi in PAGES:
    pg = doc[pi]
    png = base64.b64encode(pg.get_pixmap(matrix=fitz.Matrix(1.8, 1.8)).tobytes("png")).decode()
    pages.append({"src": png, "w": pg.rect.width, "h": pg.rect.height, "no": pi + 1})

marker = json.loads(marker_json.read_text(encoding="utf-8"))
real = json.loads(tr_json.read_text(encoding="utf-8"))
frags = []
for b in marker["blocks"]:
    if b.get("block_type") not in ("Text", "TextInlineMath", "ListItem", "Caption", "Footnote") or b.get("page") not in PAGES:
        continue
    t = " ".join(re.sub(r"<[^>]+>", "", b.get("html", "")).split())
    x0, y0, x1, y1 = b["bbox"]
    frags.append({"p": PAGES.index(b["page"]), "x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": t})
groups, cur = [], None
for f in frags:
    cur = (cur or []) + [f]
    if re.search(r"[.?!:。]\s*(\[\d+\])?\s*\d*$", f["text"]):
        groups.append(cur)
        cur = None
if cur:
    groups.append(cur)


def tex_spans(text, maths):
    s = H.escape(text)
    for i, m in enumerate(maths):
        s = s.replace(f"⟦M{i}⟧", f'<span class="tex" data-tex="{H.escape(m, quote=True)}"></span>')
    return s


def src_html(fixed):
    s = re.sub(r"<math[^>]*>(.*?)</math>",
               lambda m: f'<span class="tex" data-tex="{H.escape(H.unescape(m.group(1)), quote=True)}"></span>',
               fixed, flags=re.S)
    return re.sub(r"</?p[^>]*>", "", s)


paras = []
for fr in groups:
    fl, wh = [], []
    for j, f in enumerate(fr):
        if j:
            prev = fr[j - 1]
            wh.append(f"接續第 {pages[f['p']]['no']} 頁" if f["p"] != prev["p"]
                      else "接續右欄" if f["x0"] > prev["x1"] - 5 else "接續下方")
        item = {k: f[k] for k in ("p", "x0", "y0", "x1", "y1")}
        item["n"] = len(f["text"])
        hit = next((r for r in real if r["key"] in f["text"]), None)
        if hit:
            item["real"] = {lang: tex_spans(v["text"], hit["maths"]) for lang, v in hit["translations"].items()}
            item["src"] = src_html(hit["fixed_html"])
        fl.append(item)
    paras.append({"frags": fl, "wh": wh})

TEMPLATE = Path(sys.argv[5]).read_text(encoding="utf-8")
out = (TEMPLATE.replace("__PAGES__", json.dumps(pages))
       .replace("__PARAS__", json.dumps(paras, ensure_ascii=False)))
out_path.write_text(out, encoding="utf-8")
print("ok", len(out) // 1024, "KB;", len(paras), "PDF paragraphs")
