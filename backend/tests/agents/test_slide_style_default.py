from importlib.resources import files


def test_default_preamble_loads_and_contains_required_packages():
    tex = (files("paperhub.agents") / "slide_style_default.tex").read_text(encoding="utf-8")
    # Required structural markers
    assert "\\documentclass[aspectratio=169,14pt]{beamer}" in tex
    assert "\\input{ADDITIONAL.tex}" in tex
    assert "\\usetheme{Berlin}" in tex
    assert "\\usecolortheme{dolphin}" in tex
    assert "\\usefonttheme{professionalfonts}" in tex
    assert "\\institute{\\normalsize PaperHub}" in tex
    # Must NOT contain begin{document} — preamble ends before that line.
    assert "\\begin{document}" not in tex
