"""Derive Beamer title-page metadata (title / author / date) from the
contributing papers (PaperHub F4.2). Pure + unit-tested.

Single paper  -> the paper's own title, its authors (surnames, "et al." past 3),
                and "arXiv:<id> (<year>)" (or just <year>). ASCII-only so it
                compiles under pdflatex (no OT1-missing glyphs).
Multiple      -> the LLM talk title, with each paper's lead-author surname listed.
All fields are LaTeX-escaped for safe interpolation into \\title/\\author/\\date.
"""
from __future__ import annotations

from dataclasses import dataclass

_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
    "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_escape(text: str) -> str:
    """Escape LaTeX special characters in plain text (titles, names)."""
    out: list[str] = []
    for ch in text:
        out.append(_LATEX_SPECIALS.get(ch, ch))
    return "".join(out)


def _surname(full_name: str) -> str:
    """Last whitespace-delimited token of a 'First Last' name."""
    parts = full_name.strip().split()
    return parts[-1] if parts else full_name.strip()


def _authors_str(authors: list[str], max_names: int = 3) -> str:
    surnames = [_surname(a) for a in authors if a.strip()]
    if not surnames:
        return ""
    if len(surnames) > max_names:
        return ", ".join(surnames[:max_names]) + ", et al."
    return ", ".join(surnames)


def _date_str(year: int | None, arxiv_id: str | None) -> str:
    # ASCII only: this string is interpolated into \date{} and a single-paper
    # arXiv deck compiles under pdflatex (metropolis does not trigger xelatex),
    # where a U+00B7 middot is not in the OT1 font and breaks the build.
    if arxiv_id and year:
        return f"arXiv:{arxiv_id} ({year})"
    if arxiv_id:
        return f"arXiv:{arxiv_id}"
    if year:
        return str(year)
    return ""


@dataclass(frozen=True)
class TitleMetadata:
    title: str       # LaTeX-escaped
    author: str      # LaTeX-escaped
    date: str        # LaTeX-escaped (already safe; arXiv ids/years have no specials)


def build_title_metadata(
    papers: list[dict[str, object]], *, talk_title: str
) -> TitleMetadata:
    if not papers:
        return TitleMetadata(title=latex_escape(talk_title), author="", date="")
    if len(papers) == 1:
        p = papers[0]
        title = str(p.get("title") or talk_title)
        raw_authors = p.get("authors")
        authors_list: list[str] = [str(a) for a in raw_authors] if isinstance(raw_authors, list) else []
        author = _authors_str(authors_list)
        year_val = p.get("year")
        arxiv_val = p.get("arxiv_id")
        date = _date_str(year_val if isinstance(year_val, int) else None,
                         str(arxiv_val) if arxiv_val else None)
        return TitleMetadata(
            title=latex_escape(title),
            author=latex_escape(author),
            date=latex_escape(date),
        )
    leads: list[str] = []
    for p in papers:
        raw_authors = p.get("authors")
        a: list[str] = [str(x) for x in raw_authors] if isinstance(raw_authors, list) else []
        if a:
            leads.append(f"{_surname(a[0])} et al.")
    return TitleMetadata(
        title=latex_escape(talk_title),
        author=latex_escape(", ".join(leads)),  # ASCII separator (see _date_str)
        date="",
    )
