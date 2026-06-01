from paperhub.models.slide_domain import (
    KeyEquationBundle,
    PaperContextBundle,
)
from paperhub.pipelines.slide_pipeline.math_auditor import (
    audit_math_frames,
    has_math_block,
    role_matches_frame_topic,
)


def _bundle_with_eq(eq_role: str, latex: str) -> PaperContextBundle:
    return PaperContextBundle(
        paper_id=1,
        paper_idx=0,
        title="x",
        authors=[],
        year=2025,
        narrative_summary="",
        key_figures=[],
        key_equations=[
            KeyEquationBundle(latex=latex, role=eq_role, notation_legend="")
        ],
        section_excerpts=[],
        paper_newcommands=[],
    )


def test_has_math_block_detects_display_math():
    assert has_math_block(r"\begin{frame}{X}\[ a = b \]\end{frame}") is True


def test_has_math_block_detects_equation_env():
    assert has_math_block(r"\begin{frame}{X}\begin{equation} a=b \end{equation}\end{frame}") is True


def test_has_math_block_detects_align():
    assert has_math_block(r"\begin{frame}{X}\begin{align} a &= b \end{align}\end{frame}") is True


def test_has_math_block_detects_inline_dollar():
    assert has_math_block(r"\begin{frame}{X}some prose $a=b$ more prose\end{frame}") is True


def test_has_math_block_returns_false_for_pure_prose():
    assert has_math_block(r"\begin{frame}{X}\begin{itemize}\item hello\end{itemize}\end{frame}") is False


def test_role_matches_frame_topic_high_overlap():
    # role "visual_token_importance_score" ↔ title "Visual Token Importance Scoring"
    assert role_matches_frame_topic(
        role="visual_token_importance_score",
        topic_text="Visual Token Importance Scoring",
        threshold=0.6,
    ) is True


def test_role_matches_frame_topic_low_overlap():
    assert role_matches_frame_topic(
        role="visual_token_importance_score",
        topic_text="Introduction and Motivation",
        threshold=0.6,
    ) is False


def test_audit_flags_math_topic_frame_with_no_math():
    deck = r"""
    \begin{document}
    \begin{frame}{Visual Token Importance Scoring}
    \begin{itemize}
    \item We compute a score per token based on text-to-vision attention.
    \item Higher scores mean more relevant to the instruction.
    \end{itemize}
    \end{frame}
    \end{document}
    """
    bundle = _bundle_with_eq(
        "visual_token_importance_score",
        r"\Phi = \frac{1}{N} \sum A",
    )
    missing = audit_math_frames(deck_tex=deck, bundles=[bundle])
    assert len(missing) == 1
    assert missing[0].matched_equation_role == "visual_token_importance_score"
    assert missing[0].frame_title == "Visual Token Importance Scoring"


def test_audit_clean_when_frame_has_math():
    deck = r"""
    \begin{document}
    \begin{frame}{Visual Token Importance Scoring}
    \[ \Phi = \frac{1}{N} \sum A \]
    \end{frame}
    \end{document}
    """
    bundle = _bundle_with_eq(
        "visual_token_importance_score",
        r"\Phi = \frac{1}{N} \sum A",
    )
    assert audit_math_frames(deck_tex=deck, bundles=[bundle]) == []


def test_audit_no_match_when_topic_unrelated():
    # Frame about motivation should NOT be flagged just because the paper has an equation.
    deck = r"""
    \begin{document}
    \begin{frame}{Motivation and Background}
    \begin{itemize}\item context\end{itemize}
    \end{frame}
    \end{document}
    """
    bundle = _bundle_with_eq(
        "visual_token_importance_score",
        r"\Phi = \frac{1}{N} \sum A",
    )
    assert audit_math_frames(deck_tex=deck, bundles=[bundle]) == []
