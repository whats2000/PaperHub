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
