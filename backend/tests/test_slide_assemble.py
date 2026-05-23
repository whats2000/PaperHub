from paperhub.pipelines.slide_pipeline.assemble import (
    AssembleInput,
    assemble_deck,
    build_graphicspath,
)


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


def test_build_graphicspath_forward_slashes_windows_paths() -> None:
    """Bug A: Windows backslashes must be converted to forward slashes for TeX."""
    result = build_graphicspath([r"C:\Users\me\cache\arxiv\1706\source"])
    assert "\\" not in result.replace("\\graphicspath", "").replace("\\{", "").replace("\\}", "")
    assert "{C:/Users/me/cache/arxiv/1706/source/}" in result


def test_build_graphicspath_multiple_dirs_no_backslashes() -> None:
    result = build_graphicspath([
        r"C:\Users\me\cache\arxiv\1706\source\Figures",
        r"C:\Users\me\cache\arxiv\1706\source\vis",
    ])
    assert "{C:/Users/me/cache/arxiv/1706/source/Figures/}" in result
    assert "{C:/Users/me/cache/arxiv/1706/source/vis/}" in result
    # no raw Windows backslashes in the path segments
    # strip the TeX command itself before checking
    path_part = result.replace("\\graphicspath", "")
    assert "\\" not in path_part


def test_build_graphicspath_empty_returns_empty() -> None:
    assert build_graphicspath([]) == ""


def test_build_graphicspath_posix_paths_unchanged() -> None:
    result = build_graphicspath(["/tmp/cache/source/Figures"])
    assert "{/tmp/cache/source/Figures/}" in result
