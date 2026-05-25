# Adapted from reference/paper2slides-plus/src/beamer_utils.py @ 88515c4
# Original project: https://github.com/whats2000/paper2slides-plus (MIT).
"""Beamer utility functions for manipulating Beamer presentation frames."""

from __future__ import annotations

import re


def _parse_overlay_spec_max(spec: str) -> int:
    """Return the maximum explicit overlay index found in a Beamer overlay spec."""
    max_overlay = 0
    for part in spec.split(","):
        token = part.strip()
        if not token:
            continue

        # Handle range forms like 2-5, 2-, or -5.
        if "-" in token:
            range_match = re.fullmatch(r"(\d+)?-(\d+)?", token)
            if range_match:
                start_str, end_str = range_match.groups()
                if end_str:
                    max_overlay = max(max_overlay, int(end_str))
                elif start_str:
                    max_overlay = max(max_overlay, int(start_str))
                continue

        # Handle single number forms like 3.
        if token.isdigit():
            max_overlay = max(max_overlay, int(token))

    return max_overlay


def _count_frame_pages(frame_content: str) -> int:
    """Estimate how many PDF pages a Beamer frame expands to via overlay specs."""
    max_overlay = 1

    # Common overlay syntax appears as <...> after a command or a begin{...} block.
    overlay_pattern = r"(?:\\[a-zA-Z*]+(?:\[[^\]]*\])?|\\begin\{[^}]+\})<([^>]+)>"
    for overlay_match in re.finditer(overlay_pattern, frame_content):
        spec = overlay_match.group(1)
        max_overlay = max(max_overlay, _parse_overlay_spec_max(spec))

    return max_overlay


def annotate_overlay_frames(beamer_code: str) -> str:
    """
    Prepend an informational comment before every frame that uses overlay specs
    (e.g. \\only<N>, \\uncover<N>, \\visible<N>) so that an LLM knows to write
    a separate speaker note for each overlay page.

    The frame content itself is **not modified** — only a comment is injected::

        % [SPEAKER NOTES HINT: this frame spans 3 PDF pages due to overlays.
        %  Please write 3 separate notes: [SLIDE N], [SLIDE N+1], [SLIDE N+2].]
        \\begin{frame}{...}
          ...
        \\end{frame}

    Frames with no overlay specs are left completely unchanged.
    """
    frame_re = re.compile(r"\\begin\{frame\}.*?\\end\{frame\}", re.DOTALL)

    result_parts = []
    last_end = 0
    # Track cumulative PDF page number so hints reference the correct [SLIDE N] numbers.
    pdf_page = 0

    for m in frame_re.finditer(beamer_code):
        result_parts.append(beamer_code[last_end : m.start()])
        frame_content = m.group(0)
        num_pages = _count_frame_pages(frame_content)
        pdf_page_start = pdf_page + 1
        pdf_page += num_pages

        if num_pages > 1:
            slide_nums = ", ".join(
                f"[SLIDE {pdf_page_start + i}]" for i in range(num_pages)
            )
            comment = (
                f"% [SPEAKER NOTES HINT: This frame renders as {num_pages} PDF pages due to"
                f" \\only overlay specs.\n"
                f"%  Write {num_pages} separate notes: {slide_nums}.]\n"
            )
            result_parts.append(comment)

        result_parts.append(frame_content)
        last_end = m.end()

    result_parts.append(beamer_code[last_end:])
    return "".join(result_parts)


def extract_frames_from_beamer(beamer_code: str) -> list[tuple[int, str, int, int]]:
    """
    Extract all frames from Beamer code.

    Returns a list of tuples: (frame_number, frame_content, start_pos, end_pos)
    where frame_content includes the \\begin{frame} and \\end{frame} tags,
    and start_pos/end_pos are character positions in the original string.

    Note: If \\maketitle appears outside of a frame environment, it's treated as frame 1.
    """
    frames: list[tuple[int, str, int, int]] = []

    # Check for \maketitle that's not inside a \begin{frame}...\end{frame}
    # Look for \maketitle followed by \begin{frame} (with possible whitespace/newlines)
    maketitle_pattern = r"\\maketitle\s*(?=\\begin\{frame\})"
    maketitle_match = re.search(maketitle_pattern, beamer_code)

    # Also check if \maketitle appears before any frame at all
    first_frame_pattern = r"\\begin\{frame\}"
    first_frame_match = re.search(first_frame_pattern, beamer_code)

    if maketitle_match and (
        not first_frame_match or maketitle_match.start() < first_frame_match.start()
    ):
        # \maketitle exists before the first frame, treat it as frame 1
        maketitle_full_match = re.search(r"\\maketitle", beamer_code)
        if maketitle_full_match:
            start_pos = maketitle_full_match.start()
            end_pos = maketitle_full_match.end()
            frames.append((1, r"\maketitle", start_pos, end_pos))

    # Now extract all regular frames
    frame_pattern = r"\\begin\{frame\}.*?\\end\{frame\}"

    for match in re.finditer(frame_pattern, beamer_code, re.DOTALL):
        frame_content = match.group(0)
        start_pos = match.start()
        end_pos = match.end()
        frame_pages = _count_frame_pages(frame_content)

        # Duplicate frame mapping for each overlay page so numbering aligns with PDF pages.
        for _ in range(frame_pages):
            frame_number = len(frames) + 1
            frames.append((frame_number, frame_content, start_pos, end_pos))

    return frames


def get_frame_by_number(beamer_code: str, frame_number: int) -> str | None:
    """
    Extract a specific frame from Beamer code by frame number (1-indexed).

    Args:
        beamer_code: Full Beamer LaTeX code
        frame_number: Frame number to extract (1-indexed, matching PDF page numbers)

    Returns:
        Frame content (including \\begin{frame} and \\end{frame}, or just \\maketitle for frame 1)
        or None if not found

    Note: If \\maketitle appears outside of a frame environment, it's treated as frame 1.
    """
    frames = extract_frames_from_beamer(beamer_code)

    for frame_num, frame_content, _, _ in frames:
        if frame_num == frame_number:
            return frame_content

    return None


def _page_one_end(beamer_code: str) -> int | None:
    """
    Return the cut position that ends PDF page 1 in the source.

    - If the first real frame is the title frame (i.e. the page-1 render comes
      from ``\\begin{frame}{}\\titlepage...\\end{frame}``), the cut is just after
      its ``\\end{frame}`` so page 1 covers preamble + first frame.
    - Otherwise (synthetic ``\\maketitle`` case, or no frames at all), the cut
      is just before ``\\begin{document}`` so page 1 covers the preamble only.

    Returns ``None`` if neither anchor can be found.
    """
    frames = extract_frames_from_beamer(beamer_code)
    if frames and frames[0][1].strip() != r"\maketitle":
        return frames[0][3]
    match = re.search(r"^(.*?)\\begin\{document\}", beamer_code, re.DOTALL)
    if match:
        return match.end(1)
    return None


def get_preamble(beamer_code: str) -> str | None:
    """
    Extract the source region that renders as PDF page 1 of the Beamer document.

    This is the preamble (title, author, date, packages, style configurations,
    etc.) and — when the title page is a real ``\\begin{frame}{}\\titlepage``
    frame — that first frame as well, since both halves combine to produce the
    page-1 render. For the synthetic ``\\maketitle`` case, only the preamble is
    returned (matching the historical behavior).

    Args:
        beamer_code: Full Beamer LaTeX code

    Returns:
        Page-1 source block, or None if the structure cannot be parsed.
    """
    cut = _page_one_end(beamer_code)
    if cut is None:
        return None
    return beamer_code[:cut]


def replace_preamble(beamer_code: str, new_preamble: str) -> str | None:
    """
    Replace the page-1 source block (see ``get_preamble``) with new content.

    The replacement must include the same structural elements as the original —
    typically preamble + ``\\begin{document}`` + first title frame, or just the
    preamble in the synthetic ``\\maketitle`` case.

    Args:
        beamer_code: Full Beamer LaTeX code
        new_preamble: New page-1 source block

    Returns:
        Updated Beamer code, or None if the structure cannot be parsed.
    """
    cut = _page_one_end(beamer_code)
    if cut is None:
        return None
    return new_preamble + beamer_code[cut:]


def replace_frame_in_beamer(
    beamer_code: str, frame_number: int, new_frame_content: str
) -> str | None:
    """
    Replace a specific frame in Beamer code with new content.
    The new content can be one or multiple frames (e.g., when splitting a slide).

    Args:
        beamer_code: Full Beamer LaTeX code
        frame_number: Frame number to replace (1-indexed)
        new_frame_content: New frame content (should include \\begin{frame} and \\end{frame}).
                          Can contain multiple frames if splitting.

    Returns:
        Updated Beamer code with the frame replaced, or None if frame not found
    """
    frames = extract_frames_from_beamer(beamer_code)

    for frame_num, _, start_pos, end_pos in frames:
        if frame_num == frame_number:
            # Replace the frame at the specific position
            # new_frame_content can be one or multiple frames
            updated_code = (
                beamer_code[:start_pos] + new_frame_content + beamer_code[end_pos:]
            )
            return updated_code

    return None
