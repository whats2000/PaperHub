from paperhub.pipelines.slide_pipeline.assemble import AssembleInput, assemble_deck


def test_assemble_emits_metadata_and_title_frame():
    tex = assemble_deck(AssembleInput(
        title="Attention Is All You Need",
        author="Vaswani et al.",
        date="arXiv:1706.03762 · 2017",
        subtitle="",
        theme="metropolis",
        additional_tex_macros=[],
        cache_source_dirs=[],
        frames=["\\begin{frame}{Intro}x\\end{frame}"],
    ))
    assert "\\title{Attention Is All You Need}" in tex
    assert "\\author{Vaswani et al.}" in tex
    assert "\\date{arXiv:1706.03762 · 2017}" in tex
    assert "\\titlepage" in tex
    assert "\\maketitle" not in tex
    assert "\\begin{document}" in tex and "\\end{document}" in tex


def test_assemble_injects_title_by_default():
    """Default ``AssembleInput`` (no ``skip_title_injection``) prepends the
    auto-injected ``\\titlepage`` frame BEFORE the caller-supplied frames —
    preserves the pre-T5 behaviour for callers that do not own a title
    frame themselves."""
    tex = assemble_deck(AssembleInput(
        title="T",
        author="A",
        date="2026",
        subtitle="",
        theme="metropolis",
        additional_tex_macros=[],
        cache_source_dirs=[],
        frames=["\\begin{frame}{Intro}x\\end{frame}"],
    ))
    # Auto-injected title frame is present.
    assert tex.count("\\titlepage") == 1
    # Injection lands BEFORE the caller's frame (sentinel-ordering check).
    idx_injected = tex.find("\\titlepage")
    idx_caller = tex.find("\\begin{frame}{Intro}")
    assert -1 < idx_injected < idx_caller


def test_assemble_skips_title_injection_when_requested():
    """When ``skip_title_injection=True``, the caller-supplied title frame is
    the deck's only title page — no duplicate ``\\titlepage`` is prepended.

    F4.4 T5 review-fix: T3's ``title`` pattern template emits a
    ``\\begin{frame}[plain]\\titlepage\\end{frame}`` frame, and the T5
    planner ALWAYS emits a ``title`` PlannedSlide as slide #1. Without this
    toggle the deck would have TWO leading identical title pages."""
    caller_title_frame = "\\begin{frame}[plain]\n  \\titlepage\n\\end{frame}"
    body_frame = "\\begin{frame}{Body}content\\end{frame}"
    tex = assemble_deck(AssembleInput(
        title="T",
        author="A",
        date="2026",
        subtitle="",
        theme="metropolis",
        additional_tex_macros=[],
        cache_source_dirs=[],
        frames=[caller_title_frame, body_frame],
        skip_title_injection=True,
    ))
    # Exactly ONE \titlepage in the deck (caller's), not two.
    assert tex.count("\\titlepage") == 1
    # The caller's title frame is the first frame in document body.
    idx_begin_doc = tex.find("\\begin{document}")
    idx_caller_title = tex.find(caller_title_frame)
    idx_body = tex.find(body_frame)
    assert -1 < idx_begin_doc < idx_caller_title < idx_body
