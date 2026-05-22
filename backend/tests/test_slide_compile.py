from pathlib import Path

import pytest

from paperhub.pipelines.slide_pipeline.compile import CompileResult, compile_with_revise


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
