"""Tests for LaTeX-preamble → MathJax macro extraction (Citation Canvas math).

The Citation Canvas renders LaTeX papers to HTML via pandoc + MathJax. The
flattened body strips the preamble, so author macros (`\\vx`, `\\Ls`, `\\1`, …
from files like Goodfellow's math_commands.tex) reach MathJax undefined and
fail to render. We extract those definitions and feed them to MathJax as
`tex.macros`, alongside curated package macros (`\\mathbbm`, `\\bm`, …).
"""
from __future__ import annotations

import json
from pathlib import Path

from paperhub.pipelines.mathjax_macros import (
    CURATED_MACROS,
    build_mathjax_config_script,
    extract_macros,
    extract_macros_from_dir,
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

    def test_extract_macros_from_dir_reads_cls_and_sty(self, tmp_path: Path) -> None:
        """Author macros defined in a bundled .cls/.sty (not the main .tex
        preamble) must be harvested — arXiv:2407.15595 defines \\gD/\\sI/\\dummy
        in fairmeta.cls; the main preamble still wins on collision."""
        (tmp_path / "fairmeta.cls").write_text(
            "\\newcommand{\\dummy}{\\mathbb{m}}\n"
            "\\def\\gD{{\\mathcal{D}}}\n"
            "\\def\\sI{{\\mathbb{I}}}\n"
            "\\def\\shared{FROM_CLS}\n",
            encoding="utf-8",
        )
        (tmp_path / "extra.sty").write_text("\\def\\foo{\\alpha}\n", encoding="utf-8")
        macros = extract_macros_from_dir(
            tmp_path, preamble="\\def\\shared{FROM_PREAMBLE}"
        )
        assert macros["gD"] == r"{\mathcal{D}}"
        assert macros["sI"] == r"{\mathbb{I}}"
        assert macros["dummy"] == r"\mathbb{m}"
        assert macros["foo"] == r"\alpha"
        # Main preamble overrides a bundled-file definition of the same name.
        assert macros["shared"] == "FROM_PREAMBLE"

    def test_bundled_files_dont_clobber_curated_or_pull_layout(
        self, tmp_path: Path
    ) -> None:
        """A class file's layout redefinitions must NOT reach the math config:
        neurips_2024.sty defines \\footnotesize as \\@setfontsize... — it must
        not override the curated \\footnotesize no-op, and @-internal bodies are
        dropped entirely. Real math macros (no @) still come through."""
        (tmp_path / "conf.sty").write_text(
            "\\def\\footnotesize{\\@setfontsize\\footnotesize\\@ixpt\\@xpt}\n"
            "\\def\\headerbox{\\@tempboxa}\n"   # @-internal layout → dropped
            "\\def\\gD{{\\mathcal{D}}}\n",       # real math macro → kept
            encoding="utf-8",
        )
        macros = extract_macros_from_dir(tmp_path)
        assert "footnotesize" not in macros  # curated no-op preserved downstream
        assert "headerbox" not in macros      # @-internal body dropped
        assert macros["gD"] == r"{\mathcal{D}}"
        # And the build still emits the curated no-op for \footnotesize.
        script = build_mathjax_config_script(macros)
        cfg = json.loads(
            script[script.index("{tex:") + len("{tex:{macros:"):script.index("}};</script>")]
        )
        assert cfg["footnotesize"] == ""

    def test_extract_macros_from_dir_no_packages_is_just_preamble(
        self, tmp_path: Path
    ) -> None:
        macros = extract_macros_from_dir(tmp_path, preamble="\\def\\a{\\beta}")
        assert macros == {"a": r"\beta"}

    def test_capitalized_wide_tilde_maps_to_widetilde(self) -> None:
        """\\Tilde (used inside author macros, never reaching extract_macros)
        isn't in MathJax's build; it must map to the native \\widetilde."""
        script = build_mathjax_config_script()
        start = script.index("{tex:") + len("{tex:{macros:")
        end = script.index("}};</script>")
        macros = json.loads(script[start:end])
        assert macros.get("Tilde") == [r"\widetilde{#1}", 1]

    def test_font_size_switches_are_noop_macros(self) -> None:
        """\\footnotesize etc. aren't in MathJax's default build; they must be
        defined as no-ops so formula annotations like
        \\text{\\footnotesize $\\because …$} render instead of erroring."""
        script = build_mathjax_config_script()
        start = script.index("{tex:") + len("{tex:{macros:")
        end = script.index("}};</script>")
        macros = json.loads(script[start:end])
        for size in ("footnotesize", "small", "scriptsize", "tiny", "Large"):
            assert macros.get(size) == "", f"{size} must map to a no-op"

    def test_spacing_commands_are_noops(self) -> None:
        """\\vspace/\\hspace can land inside math (arXiv:2512.04952 renders
        \\[\\vspace{-0.5em}\\]); MathJax lacks them. Map to arg-consuming no-ops
        so the math doesn't error."""
        script = build_mathjax_config_script()
        start = script.index("{tex:") + len("{tex:{macros:")
        end = script.index("}};</script>")
        macros = json.loads(script[start:end])
        assert macros.get("vspace") == ["", 1]  # consumes the dimension arg
        assert macros.get("hspace") == ["", 1]
        assert macros.get("bigskip") == ""  # bare, no-arg

    def test_dagger_text_symbols_map_to_math_equivalents(self) -> None:
        """\\dag/\\ddag are LaTeX text symbols papers use in math for footnote
        markers ($^{\\dag}$); MathJax's math build has only \\dagger/\\ddagger,
        so they must be mapped or the superscript renders as an error."""
        script = build_mathjax_config_script()
        start = script.index("{tex:") + len("{tex:{macros:")
        end = script.index("}};</script>")
        macros = json.loads(script[start:end])
        assert macros.get("dag") == r"\dagger"
        assert macros.get("ddag") == r"\ddagger"

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
