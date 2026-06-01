"""F4.5 math-frame auditor (contract #2).

A frame whose title or body topic-keyword-matches a key_equation.role MUST
contain a math block. compile_check() returns this auditor's output as
unrendered_math_frames[]; the slide_agent's done() is REJECTED until empty.

Role-keyword overlap is deterministic: split role on `_`, lowercase, intersect
with the frame's title + body keyword set. Threshold default 0.6 to avoid
false positives on incidental word overlap.
"""
from __future__ import annotations

import re

from paperhub.models.slide_domain import PaperContextBundle, UnrenderedMathFrame

# Frame extractor — same regex as overflow_detector to stay consistent.
_FRAME_RE = re.compile(
    r"\\begin\{frame\}(?:\[[^\]]*\])?(?:\{([^}]*)\})?(.*?)\\end\{frame\}",
    re.DOTALL,
)
_MATH_BLOCK_RE = re.compile(
    r"\\\[|\\begin\{equation\}|\\begin\{equation\*\}|\\begin\{align\}|\\begin\{align\*\}|\\begin\{multline\}|\\begin\{gather\}|\$[^$\n]+\$"
)

# Common stop-words excluded from token-overlap (English).
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "with",
        "is", "are", "we", "our", "this", "that", "these", "those", "by", "at",
        "as", "be", "from", "via",
    }
)


def has_math_block(frame_tex: str) -> bool:
    return _MATH_BLOCK_RE.search(frame_tex) is not None


def _tokenize(text: str) -> set[str]:
    """Lowercase, split on non-alpha, drop stop-words + short tokens."""
    tokens = re.findall(r"[a-zA-Z]+", text.lower())
    return {t for t in tokens if len(t) >= 3 and t not in _STOP_WORDS}


def role_matches_frame_topic(*, role: str, topic_text: str, threshold: float = 0.6) -> bool:
    """Token-overlap match between role string and frame topic.

    overlap_ratio = |role_tokens ∩ topic_tokens| / |role_tokens|

    The role's token set is the denominator (not Jaccard) so a short role like
    'kl_divergence' matches a long descriptive frame title even if the title
    has many unrelated extra words.
    """
    role_tokens = _tokenize(role.replace("_", " "))
    if not role_tokens:
        return False
    topic_tokens = _tokenize(topic_text)
    overlap = len(role_tokens & topic_tokens)
    return (overlap / len(role_tokens)) >= threshold


def _extract_body_text(frame_tex: str) -> str:
    """Strip LaTeX commands and braces from frame body for keyword extraction."""
    body = re.sub(r"\\begin\{frame\}(?:\[[^\]]*\])?\{[^}]*\}", "", frame_tex)
    body = re.sub(r"\\end\{frame\}", "", body)
    body = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?", " ", body)
    body = re.sub(r"[{}\[\]]", " ", body)
    return body


def audit_math_frames(
    *, deck_tex: str, bundles: list[PaperContextBundle], threshold: float = 0.6
) -> list[UnrenderedMathFrame]:
    """Find frames whose topic matches a key_equation.role but have no math block."""
    missing: list[UnrenderedMathFrame] = []
    for idx, m in enumerate(_FRAME_RE.finditer(deck_tex)):
        title = (m.group(1) or "").strip()
        frame_tex = m.group(0)
        if has_math_block(frame_tex):
            continue
        topic_text = title + " " + _extract_body_text(frame_tex)
        # Find the FIRST (paper_idx, equation) whose role matches this topic.
        for bundle in bundles:
            matched = None
            for eq in bundle.key_equations:
                if role_matches_frame_topic(
                    role=eq.role, topic_text=topic_text, threshold=threshold
                ):
                    matched = eq
                    break
            if matched is not None:
                missing.append(
                    UnrenderedMathFrame(
                        frame_index=idx,
                        frame_title=title or "(no title)",
                        matched_equation_role=matched.role,
                        matched_equation_latex=matched.latex,
                        paper_idx=bundle.paper_idx,
                        recommendation=(
                            f"replace_frame {idx} with equation_centered layout including "
                            f"the equation: {matched.latex[:80]}"
                        ),
                    )
                )
                break  # one match per frame is enough
    return missing
