from pathlib import Path

import pytest

from paperhub.pipelines.slide_pipeline.compile import (
    CompileResult,
    _has_overfull_vbox,
    compile_with_revise,
)


@pytest.mark.asyncio
async def test_compile_success_first_try(tmp_path: Path, monkeypatch) -> None:
    workdir = tmp_path / "slides"
    workdir.mkdir()
    (workdir / "deck.tex").write_text("\\documentclass{beamer}\\begin{document}\\end{document}")

    def fake_run(cmd, cwd=None, **kw):
        Path(cwd, "deck.pdf").write_bytes(b"%PDF-1.4 fake")
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr("paperhub.pipelines.slide_pipeline.compile.subprocess.run", fake_run)

    async def never_revise(log: str, tex: str) -> str:
        raise AssertionError("revise called on success")

    res: CompileResult = await compile_with_revise(
        tex=(workdir / "deck.tex").read_text(),
        workdir=workdir, tex_name="deck.tex", revise=never_revise, max_retries=2,
    )
    assert res.ok is True
    assert res.attempts == 1
    assert (workdir / "deck.pdf").exists()


@pytest.mark.asyncio
async def test_compile_revises_then_succeeds(tmp_path: Path, monkeypatch) -> None:
    workdir = tmp_path / "slides"
    workdir.mkdir()
    calls = {"n": 0}

    def fake_run(cmd, cwd=None, **kw):
        import subprocess
        calls["n"] += 1
        if calls["n"] == 1:
            return subprocess.CompletedProcess(cmd, 1, "! LaTeX Error", "")
        Path(cwd, "deck.pdf").write_bytes(b"%PDF-1.4 fake")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr("paperhub.pipelines.slide_pipeline.compile.subprocess.run", fake_run)

    async def revise(log: str, tex: str) -> str:
        return tex + "\n% fixed"

    res = await compile_with_revise(
        tex="\\documentclass{beamer}\\begin{document}\\end{document}",
        workdir=workdir, tex_name="deck.tex", revise=revise, max_retries=2,
    )
    assert res.ok is True
    assert res.attempts == 2
    assert "% fixed" in res.tex


@pytest.mark.asyncio
async def test_compile_fails_after_exhausting_retries(tmp_path: Path, monkeypatch) -> None:
    workdir = tmp_path / "slides"
    workdir.mkdir()

    def always_fail(cmd, cwd=None, **kw):
        import subprocess
        return subprocess.CompletedProcess(cmd, 1, "! LaTeX Error", "")
    monkeypatch.setattr("paperhub.pipelines.slide_pipeline.compile.subprocess.run", always_fail)

    revise_calls = {"n": 0}

    async def revise(log: str, tex: str) -> str:
        revise_calls["n"] += 1
        return tex

    res = await compile_with_revise(
        tex="\\documentclass{beamer}\\begin{document}\\end{document}",
        workdir=workdir, tex_name="deck.tex", revise=revise, max_retries=2,
    )
    assert res.ok is False
    assert res.attempts == 3  # initial + 2 retries
    assert res.page_count == 0
    assert revise_calls["n"] == 2  # revise called once per retry, not after the last attempt


@pytest.mark.asyncio
async def test_compile_handles_timeout(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    workdir = tmp_path / "slides"
    workdir.mkdir()

    def timeout_run(cmd, cwd=None, **kw):
        raise subprocess.TimeoutExpired(cmd, 300)
    monkeypatch.setattr("paperhub.pipelines.slide_pipeline.compile.subprocess.run", timeout_run)

    async def revise(log: str, tex: str) -> str:
        return tex

    res = await compile_with_revise(
        tex="\\documentclass{beamer}\\begin{document}\\end{document}",
        workdir=workdir, tex_name="deck.tex", revise=revise, max_retries=0,
    )
    assert res.ok is False
    assert "timed out" in res.log


# ---------------------------------------------------------------------------
# _has_overfull_vbox unit tests
# ---------------------------------------------------------------------------

def test_has_overfull_vbox_detects_warning() -> None:
    assert _has_overfull_vbox("Overfull \\vbox (12pt too high) has occurred")
    assert _has_overfull_vbox("some preamble\nOverfull \\vbox (3pt too high) while \\output\nmore text")


def test_has_overfull_vbox_returns_false_for_clean_log() -> None:
    assert not _has_overfull_vbox("")
    assert not _has_overfull_vbox("Output written on deck.pdf (10 pages).")
    assert not _has_overfull_vbox("Overfull \\hbox (5pt too wide)")  # hbox is different


# ---------------------------------------------------------------------------
# Overfull vbox triggers a revise cycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_overfull_vbox_triggers_revise(tmp_path: Path, monkeypatch) -> None:
    """A rc=0 run that contains Overfull \\vbox must trigger one revise + recompile."""
    workdir = tmp_path / "slides"
    workdir.mkdir()
    calls: dict[str, int] = {"n": 0}

    def fake_run(cmd, cwd=None, **kw):
        import subprocess
        calls["n"] += 1
        # Both runs produce a PDF (rc=0); only the first has the Overfull warning.
        Path(cwd, "deck.pdf").write_bytes(b"%PDF-1.4 fake")
        stdout = "Overfull \\vbox (12pt too high) while \\output" if calls["n"] == 1 else "Output written on deck.pdf"
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr("paperhub.pipelines.slide_pipeline.compile.subprocess.run", fake_run)

    revised: dict[str, int] = {"n": 0}

    async def revise(log: str, tex: str) -> str:
        revised["n"] += 1
        return tex + "\n% tightened"

    res = await compile_with_revise(
        tex="\\documentclass{beamer}\\begin{document}\\end{document}",
        workdir=workdir,
        tex_name="deck.tex",
        revise=revise,
        max_retries=2,
    )

    assert res.ok is True
    assert revised["n"] == 1          # Overfull run was revised exactly once
    assert calls["n"] == 2            # two pdflatex invocations total
    assert "% tightened" in res.tex   # revised source was used


@pytest.mark.asyncio
async def test_overfull_vbox_exhausted_retries_still_emits_pdf(tmp_path: Path, monkeypatch) -> None:
    """When every attempt has Overfull \\vbox and retries are exhausted,
    a PDF still exists so we keep ok=True (degraded deck, not lost deck)."""
    workdir = tmp_path / "slides"
    workdir.mkdir()

    def always_overfull(cmd, cwd=None, **kw):
        import subprocess
        Path(cwd, "deck.pdf").write_bytes(b"%PDF-1.4 fake")
        return subprocess.CompletedProcess(cmd, 0, "Overfull \\vbox (20pt too high)", "")

    monkeypatch.setattr("paperhub.pipelines.slide_pipeline.compile.subprocess.run", always_overfull)

    revise_calls: dict[str, int] = {"n": 0}

    async def revise(log: str, tex: str) -> str:
        revise_calls["n"] += 1
        return tex  # unchanged — still overflows

    res = await compile_with_revise(
        tex="\\documentclass{beamer}\\begin{document}\\end{document}",
        workdir=workdir,
        tex_name="deck.tex",
        revise=revise,
        max_retries=2,
    )

    # PDF exists → ok=True (degraded but not lost), revise was called on each non-final attempt
    assert res.ok is True
    assert revise_calls["n"] == 2     # one per allowed retry (attempts 1 and 2), not after the last
    assert (workdir / "deck.pdf").exists()
