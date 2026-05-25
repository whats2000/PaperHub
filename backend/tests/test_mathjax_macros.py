"""Tests for LaTeX-preamble → MathJax macro extraction (Citation Canvas math).

The Citation Canvas renders LaTeX papers to HTML via pandoc + MathJax. The
flattened body strips the preamble, so author macros (`\\vx`, `\\Ls`, `\\1`, …
from files like Goodfellow's math_commands.tex) reach MathJax undefined and
fail to render. We extract those definitions and feed them to MathJax as
`tex.macros`, alongside curated package macros (`\\mathbbm`, `\\bm`, …).
"""
from __future__ import annotations

import json

from paperhub.pipelines.mathjax_macros import (
    CURATED_MACROS,
    build_mathjax_config_script,
    extract_macros,
)


class TestExtractMacros:
    def test_simple_newcommand(self) -> None:
        assert extract_macros(r"\newcommand{\Ls}{\mathcal{L}}") == {"Ls": r"\mathcal{L}"}

    def test_newcommand_with_nested_braces(self) -> None:
        assert extract_macros(r"\newcommand{\valid}{\mathcal{D_{\mathrm{valid}}}}") == {
            "valid": r"\mathcal{D_{\mathrm{valid}}}",
        }

    def test_newcommand_with_args(self) -> None:
        assert extract_macros(r"\newcommand{\norm}[1]{\lVert #1 \rVert}") == {
            "norm": [r"\lVert #1 \rVert", 1],
        }

    def test_newcommand_with_optional_default(self) -> None:
        assert extract_macros(r"\newcommand{\diff}[2][x]{\frac{d#2}{d#1}}") == {
            "diff": [r"\frac{d#2}{d#1}", 2, "x"],
        }

    def test_renew_and_provide(self) -> None:
        got = extract_macros(
            r"\renewcommand{\vec}{\mathbf}" "\n" r"\providecommand{\R}{\mathbb{R}}"
        )
        assert got == {"vec": r"\mathbf", "R": r"\mathbb{R}"}

    def test_declare_math_operator(self) -> None:
        assert extract_macros(r"\DeclareMathOperator{\argmax}{arg\,max}") == {
            "argmax": r"\operatorname{arg\,max}",
        }

    def test_declare_math_operator_starred(self) -> None:
        assert extract_macros(r"\DeclareMathOperator*{\E}{\mathbb{E}}") == {
            "E": r"\operatorname*{\mathbb{E}}",
        }

    def test_simple_def(self) -> None:
        assert extract_macros(r"\def\R{\mathbb{R}}") == {"R": r"\mathbb{R}"}

    def test_single_char_name(self) -> None:
        # The indicator macro Goodfellow's file defines as `\1`.
        assert extract_macros(r"\newcommand{\1}{\mathbb{1}}") == {"1": r"\mathbb{1}"}

    def test_ignores_commented_out_definitions(self) -> None:
        assert extract_macros("% \\newcommand{\\foo}{bar}") == {}

    def test_keeps_escaped_percent(self) -> None:
        # `\%` is a literal percent, not a comment start.
        assert extract_macros(r"\newcommand{\pct}{100\%}") == {"pct": r"100\%"}

    def test_later_definition_wins(self) -> None:
        got = extract_macros(r"\newcommand{\x}{a}" "\n" r"\renewcommand{\x}{b}")
        assert got == {"x": "b"}

    def test_skips_unparseable_without_raising(self) -> None:
        # An unbalanced body must not blow up extraction of the good one.
        got = extract_macros(r"\newcommand{\ok}{fine}" "\n" r"\newcommand{\bad}{oops")
        assert got == {"ok": "fine"}

    def test_empty_input(self) -> None:
        assert extract_macros("") == {}


class TestBuildScript:
    def test_includes_curated_macros(self) -> None:
        script = build_mathjax_config_script()
        assert "window.MathJax" in script
        assert "mathbbm" in script
        # Curated mappings are present.
        assert any("mathbbm" in k for k in CURATED_MACROS)

    def test_merges_and_overrides_with_extracted(self) -> None:
        script = build_mathjax_config_script({"Ls": r"\mathcal{L}"})
        # Extract the JSON object from `window.MathJax={tex:{macros:<JSON>}};`.
        start = script.index("{tex:") + len("{tex:{macros:")
        end = script.index("}};</script>")
        macros = json.loads(script[start:end])
        assert macros["Ls"] == r"\mathcal{L}"
        assert macros["mathbbm"] == [r"\mathbb{#1}", 1]

    def test_extracted_overrides_curated(self) -> None:
        script = build_mathjax_config_script({"bm": r"\mathbf"})
        start = script.index("{tex:") + len("{tex:{macros:")
        end = script.index("}};</script>")
        macros = json.loads(script[start:end])
        assert macros["bm"] == r"\mathbf"

    def test_escapes_script_close_sequence(self) -> None:
        # A body containing </script> must not break out of the <script> tag.
        script = build_mathjax_config_script({"evil": r"</script>"})
        assert "</script>" not in script[: script.rindex("</script>")]
