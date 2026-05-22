from paperhub.pipelines.slide_pipeline.assemble import AssembleInput, assemble_deck


def test_assemble_includes_graphicspath_and_macros_and_frames() -> None:
    out = assemble_deck(AssembleInput(
        title="MoE Routing: A Comparison",
        theme="metropolis",
        additional_tex_macros=["\\newcommand{\\bx}{\\mathbf{x}}"],
        cache_source_dirs=["/ws/papers_cache/arxiv/2403.01234/source", "/ws/papers_cache/arxiv/2401.05678/source"],
        frames=["\\begin{frame}{Intro}\\end{frame}", "\\begin{frame}{Method}\\end{frame}"],
    ))
    assert "\\usetheme{metropolis}" in out
    assert "\\graphicspath{ {/ws/papers_cache/arxiv/2403.01234/source/} {/ws/papers_cache/arxiv/2401.05678/source/} }" in out
    assert "\\newcommand{\\bx}" in out
    assert "{Intro}" in out and "{Method}" in out
    assert out.strip().endswith("\\end{document}")
    assert "\\title{MoE Routing: A Comparison}" in out
