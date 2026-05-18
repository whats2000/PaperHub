from pathlib import Path
from unittest.mock import MagicMock, patch

from paperhub.pipelines.arxiv_client import (
    ArxivResult,
    download_arxiv_source,
    search_arxiv,
)


def test_search_arxiv_returns_typed_results() -> None:
    fake_result = MagicMock()
    fake_result.entry_id = "http://arxiv.org/abs/2403.01234v1"
    fake_result.title = "A Test Paper"
    fake_result.authors = [MagicMock(name="Author One"), MagicMock(name="Author Two")]
    fake_result.authors[0].name = "Author One"
    fake_result.authors[1].name = "Author Two"
    fake_result.summary = "An abstract."
    fake_result.published.year = 2024

    fake_search = MagicMock()
    fake_search.results.return_value = iter([fake_result])

    with patch("paperhub.pipelines.arxiv_client.arxiv.Search", return_value=fake_search):
        results = search_arxiv("mixture of experts", max_results=1)

    assert len(results) == 1
    r = results[0]
    assert isinstance(r, ArxivResult)
    assert r.arxiv_id == "2403.01234"
    assert r.title == "A Test Paper"
    assert r.authors == ["Author One", "Author Two"]
    assert r.year == 2024
    assert r.abstract == "An abstract."


def test_download_arxiv_source_writes_to_cache(tmp_path: Path) -> None:
    fake_result = MagicMock()
    fake_result.download_source = MagicMock(
        return_value=str(tmp_path / "downloaded.tar.gz")
    )
    fake_search = MagicMock()
    fake_search.results.return_value = iter([fake_result])

    # Pre-create the "downloaded" tarball with a minimal layout.
    import tarfile
    src_file = tmp_path / "main.tex"
    src_file.write_text(r"\documentclass{article}\begin{document}Hi\end{document}")
    tar_path = tmp_path / "downloaded.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(src_file, arcname="main.tex")

    with patch("paperhub.pipelines.arxiv_client.arxiv.Search", return_value=fake_search):
        source_dir = download_arxiv_source("2403.01234", cache_root=tmp_path / "cache")

    assert source_dir.exists()
    assert (source_dir / "main.tex").exists()
    assert source_dir.parent.name == "2403.01234"
