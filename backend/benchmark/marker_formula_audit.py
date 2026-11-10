"""How often is Marker's LaTeX for a display equation wrong?

For each display equation the agent reads in a Marker chunk (``--source
chunks``, default) or each Marker ``Equation`` block in asset/blocks.json
(``--source blocks``, what the PDF translation card renders — a separate
Marker run), crop the equation from the paper's PDF and ask a vision model
whether the LaTeX matches the printed equation: ``match`` / ``minor`` (no change in
meaning) / ``wrong`` (a reader would compute or cite something different).
Writes ``audit.csv`` + the crops for manual spot checks and prints the rates.

    uv run python -m benchmark.marker_formula_audit --db <paperhub.db> \\
        [--sample 40] [--prompt a|b] [--model gemini/gemini-2.5-pro]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import html
import json
import random
import re
import sqlite3
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import litellm
from pydantic import BaseModel

from benchmark.judge import DEFAULT_JUDGE_MODEL, JUDGE_TEMPERATURE, load_env
from paperhub.pipelines.page_pdf import page_pdf_path

CROP_PAD_PT = 4.0
CROP_DPI = 200


@dataclass(frozen=True)
class EqBlock:
    paper: int
    block_id: str
    page: int                                   # 0-based, as Marker stores it
    bbox: tuple[float, float, float, float]     # PDF points, top-left origin
    latex: str
    pdf_path: Path


class EqVerdict(BaseModel):
    verdict: Literal["match", "minor", "wrong"]
    error: str = ""


Judge = Callable[[EqBlock, bytes], Awaitable[EqVerdict]]

_MATH = re.compile(r"<math\b[^>]*>(.*?)</math>", re.S)


def latex_of(block_html: str) -> str:
    """Marker's LaTeX inside the block's ``<math>`` element(s)."""
    parts = [html.unescape(m.group(1)).strip() for m in _MATH.finditer(block_html)]
    return "\n".join(p for p in parts if p)


_DISPLAY = re.compile(r"\$\$(.+?)\$\$", re.S)


def _sample(out: list[EqBlock], sample: int, seed: int) -> list[EqBlock]:
    if sample and sample < len(out):
        out = sorted(random.Random(seed).sample(out, sample),
                     key=lambda e: (e.paper, e.page, e.bbox[1]))
    return out


def equation_blocks_of(pid: int, root: Path) -> list[EqBlock]:
    """One paper's Marker equation blocks with LaTeX (asset/blocks.json)."""
    blocks_json = root / "asset" / "blocks.json"
    pdf = page_pdf_path(root)
    if not blocks_json.is_file() or pdf is None:
        return []
    out: list[EqBlock] = []
    for b in json.loads(blocks_json.read_text(encoding="utf-8")).get("blocks", []):
        if b.get("block_type") != "Equation" or not b.get("bbox"):
            continue
        latex = latex_of(b.get("html") or "")
        if not latex:
            continue
        x0, y0, x1, y1 = (float(v) for v in b["bbox"])
        out.append(EqBlock(paper=pid, block_id=str(b["block_id"]), page=int(b["page"]),
                           bbox=(x0, y0, x1, y1), latex=latex, pdf_path=pdf))
    return out


def equation_blocks(db_path: Path, *, sample: int = 0, seed: int = 0) -> list[EqBlock]:
    """Every Marker equation block with LaTeX (what the PDF translation card
    renders), in paper then block order, or a seeded random ``sample``."""
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT id, source_dir_path FROM paper_content "
            "WHERE source_dir_path IS NOT NULL ORDER BY id").fetchall()
    out = [b for pid, root in rows for b in equation_blocks_of(int(pid), Path(root))]
    return _sample(out, sample, seed)


def chunk_equations(db_path: Path, *, sample: int = 0, seed: int = 0) -> list[EqBlock]:
    """The display equations the agent actually reads: each ``$$..$$`` in a
    Marker chunk (its own transcription, from the ingest run), located by the
    Marker equation block on the chunk's page inside the chunk's box (the
    closest LaTeX). Chunks without a page (PyMuPDF text, old Marker chunks)
    carry no LaTeX to audit and cannot be located."""
    import difflib

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        roots = dict(conn.execute(
            "SELECT id, source_dir_path FROM paper_content WHERE source_dir_path IS NOT NULL"))
        chunks = conn.execute(
            "SELECT id, paper_content_id, page, bbox, text FROM chunks "
            "WHERE page IS NOT NULL AND bbox IS NOT NULL AND text LIKE '%$$%' "
            "ORDER BY paper_content_id, id").fetchall()
    blocks_by_paper: dict[int, list[EqBlock]] = {}
    out: list[EqBlock] = []
    for cid, pid, page, bbox_s, text in chunks:
        if pid not in blocks_by_paper:
            root = roots.get(pid)
            blocks_by_paper[pid] = (
                [b for b in equation_blocks_of(int(pid), Path(root))] if root else [])
        cx0, cy0, cx1, cy1 = json.loads(bbox_s)
        cands = [b for b in blocks_by_paper[pid] if b.page == page
                 and cx0 <= (b.bbox[0] + b.bbox[2]) / 2 <= cx1
                 and cy0 <= (b.bbox[1] + b.bbox[3]) / 2 <= cy1]
        for k, m in enumerate(_DISPLAY.finditer(text)):
            latex = m.group(1).strip()
            if not cands or not latex:
                continue
            best = max(cands, key=lambda b: difflib.SequenceMatcher(
                None, b.latex, latex).ratio())
            out.append(EqBlock(paper=int(pid), block_id=f"chunk:{cid}#{k}", page=best.page,
                               bbox=best.bbox, latex=latex, pdf_path=best.pdf_path))
    return _sample(out, sample, seed)


def crop_block(block: EqBlock, *, dpi: int = CROP_DPI) -> bytes:
    """The block's region of its page, padded, as PNG bytes."""
    import pymupdf

    with pymupdf.open(block.pdf_path) as doc:  # type: ignore[no-untyped-call]
        page = doc[block.page]
        x0, y0, x1, y1 = block.bbox
        clip = pymupdf.Rect(  # type: ignore[no-untyped-call]
            x0 - CROP_PAD_PT, y0 - CROP_PAD_PT, x1 + CROP_PAD_PT, y1 + CROP_PAD_PT,
        ) & page.rect
        png: bytes = page.get_pixmap(dpi=dpi, clip=clip).tobytes("png")
    return png


# Two judge-prompt variants (writing-agent-prompts loop): A states the rules,
# B adds worked examples for the minor / wrong boundary.
_SYSTEM_A = """\
You check whether a LaTeX transcription matches the equation printed in an image.

Verdicts:
- match: the LaTeX renders the same mathematics as the image. Spacing, \\left/\\right, \
font commands and whether the equation number is written do not matter.
- minor: a difference that leaves the meaning unchanged, such as \\cdot versus \
juxtaposition, a missing trailing comma or full stop, or bracket sizing.
- wrong: a difference that makes a reader compute or cite something else: a wrong, \
missing or extra symbol, subscript, superscript, operator, sign, index, term or \
fraction structure, or part of the printed equation left out.
If the image shows no equation, or is cut off so you cannot compare, answer wrong \
with error "unreadable crop".

Reply with JSON only: {"verdict": "match" | "minor" | "wrong", "error": "<the first \
meaning-changing difference for wrong, the difference for minor, else empty>"}"""

_SYSTEM_B = _SYSTEM_A + """

Examples (image → LaTeX → verdict):
- printed x_{t+1} = x_t - \\eta \\nabla L; LaTeX x_{t+1} = x_t - \\eta \\nabla L (3) → match
- printed a·b, then a full stop; LaTeX a b → minor (multiplication dot and full stop)
- printed \\sum_{i=1}^{N}; LaTeX \\sum_{i=0}^{N} → wrong (lower limit 1 became 0)
- printed W_q^T; LaTeX W_q → wrong (transpose dropped)
- printed two stacked lines; LaTeX only the first → wrong (second line missing)"""

PROMPTS = {"a": _SYSTEM_A, "b": _SYSTEM_B}

_USER = """\
<marker_latex>
{latex}
</marker_latex>
The image is the equation as printed in the paper. Does the LaTeX match it?"""


def llm_judge(model: str, prompt: str) -> Judge:
    system = PROMPTS[prompt]

    async def judge(block: EqBlock, png: bytes) -> EqVerdict:
        url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        resp = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": _USER.format(latex=block.latex)},
                    {"type": "image_url", "image_url": {"url": url}},
                ]},
            ],
            response_format=EqVerdict,
            temperature=JUDGE_TEMPERATURE,
        )
        return EqVerdict.model_validate_json(resp["choices"][0]["message"]["content"])

    return judge


def _crop_name(block: EqBlock) -> str:
    return f"crops/p{block.paper}-{re.sub(r'[^A-Za-z0-9]+', '_', block.block_id).strip('_')}.png"


async def run_audit(blocks: list[EqBlock], *, judge: Judge, out_dir: Path,
                    concurrency: int = 8) -> dict[str, float | int]:
    """Judge every block, write ``audit.csv`` + crops into ``out_dir``, and
    return the rates over the judged blocks (``failed`` = judge errors)."""
    (out_dir / "crops").mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)

    async def one(block: EqBlock) -> tuple[EqBlock, str, str, str]:
        png = await asyncio.to_thread(crop_block, block)
        name = _crop_name(block)
        (out_dir / name).write_bytes(png)
        async with sem:
            try:
                v = await judge(block, png)
            except Exception as exc:  # noqa: BLE001 - a failed call is recorded, not fatal
                return block, "failed", f"{type(exc).__name__}: {exc}"[:300], name
        return block, v.verdict, v.error, name

    results = await asyncio.gather(*(one(b) for b in blocks))
    with (out_dir / "audit.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["paper", "block_id", "page", "bbox", "latex", "verdict", "error", "crop"])
        for b, verdict, error, name in results:
            w.writerow([b.paper, b.block_id, b.page, ",".join(f"{v:.1f}" for v in b.bbox),
                        b.latex, verdict, error, name])
    counts = Counter(verdict for _, verdict, _, _ in results)
    n = len(results) - counts["failed"]
    return {"n": n, **{k: (counts[k] / n if n else 0.0) for k in ("match", "minor", "wrong")},
            "failed": counts["failed"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="workspace paperhub.db (read-only)")
    ap.add_argument("--sample", type=int, default=0, help="random sample size (0 = all)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--source", choices=["chunks", "blocks"], default="chunks",
                    help="chunks: the equations the agent reads; blocks: asset/blocks.json "
                    "(what the PDF translation card renders)")
    ap.add_argument("--prompt", choices=sorted(PROMPTS), default="a")
    ap.add_argument("--model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--out", default="benchmark/results/marker-formula-audit")
    ap.add_argument("--env", default=".env")
    args = ap.parse_args()
    load_env(args.env)
    pick = chunk_equations if args.source == "chunks" else equation_blocks
    blocks = pick(Path(args.db), sample=args.sample, seed=args.seed)
    out_dir = Path(args.out) / f"{args.source}-prompt-{args.prompt}"
    rates = asyncio.run(run_audit(blocks, judge=llm_judge(args.model, args.prompt),
                                  out_dir=out_dir))
    print(json.dumps({"source": args.source, "blocks": len(blocks), "model": args.model,
                      "prompt": args.prompt, **rates}, indent=2))
    print(f"CSV + crops: {out_dir}")


if __name__ == "__main__":
    main()
