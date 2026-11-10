"""A quotation cited to a chunk must be in that chunk: a misplaced citation
moves to the read chunk that holds the words, an unfounded one is removed
(run 1419 quoted chunk 82425's sentence as [chunk:82424])."""
from __future__ import annotations

import aiosqlite
import pytest

from paperhub.agents.core.agent import Final
from paperhub.agents.core.citations import verify_quotes

from .test_agent_loop import (
    FakeAdapter,
    _answer,
    _calls,
    _drive,
    _enable,
    _read_registry,
    _rows,
    _setup,
)
from .test_read_tools import _paper

A = ("There is a trade-off between AP and IoU. Ultralytics's YOLOv5s-segmentation "
     "excels in AP, making it suitable for tasks prioritizing\ndetection speed and "
     "efficiency, but its low IoU indicates less precise masks.")
B = "Die defect inspection performance of models was compared on the testing set."


def test_quote_in_the_cited_chunk_stays() -> None:
    text = 'It "excels in AP, making it suitable for tasks prioritizing detection speed" [chunk:2].'
    out, fixes = verify_quotes(text, {1: B, 2: A})
    assert out == text and fixes == []


def test_quote_in_another_read_chunk_moves_the_citation() -> None:
    text = 'YOLOv5s "excels in AP, making it suitable for tasks prioritizing detection speed" [chunk:1].'
    out, fixes = verify_quotes(text, {1: B, 2: A})
    assert out == text.replace("[chunk:1]", "[chunk:2]")
    assert fixes == [{"quote": "excels in AP, making it suitable for tasks prioritizing "
                               "detection speed", "cited": [1], "action": "moved", "to": [2]}]


def test_quote_found_nowhere_is_marked_unsourced() -> None:
    text = 'The authors claim it "runs at 120 FPS on a Jetson Nano" [chunk:1, 2]. Next.'
    out, fixes = verify_quotes(text, {1: B, 2: A})
    assert out == ('The authors claim it "runs at 120 FPS on a Jetson Nano" '
                   '[unsourced:quote_not_found]. Next.')
    assert fixes[0]["action"] == "marked" and fixes[0]["cited"] == [1, 2]


def test_tolerates_pdf_text_and_ellipsis() -> None:
    text = ("“Ultralytics’s YOLOv5s-segmentation excels in AP … but its low IoU "
            "indicates less precise masks” [chunk:2]")
    out, fixes = verify_quotes(text, {2: A.replace("efficiency", "eﬃciency")})
    assert fixes == [] and out == text


C1 = ("Firstly, the memory and computational complexity required to compute the "
      "attention matrix is quadratic in the input sequence length, i.e., N x N.")
C2 = ("In particular, the QK matrix multiplication operation alone consumes N^2 "
      "time and memory. This restricts the usage of self-attention.")


def test_a_quote_cited_sentence_by_sentence_inside_is_checked_per_sentence() -> None:
    """Run 1263: a two-chunk quotation carried its own citations inside; the
    whole quotation was checked against the next paragraph's citation and that
    sound citation was marked unsourced."""
    text = ('> "Firstly, the memory and computational complexity required to compute '
            'the attention matrix is quadratic in the input sequence length [chunk:1]. '
            'In particular, the QK matrix multiplication operation alone consumes N^2 '
            'time and memory. [chunk:2]"\n\n作者也提到這限制了長序列的應用 [chunk:2]。')
    out, fixes = verify_quotes(text, {1: C1, 2: C2})
    assert out == text and fixes == []


def test_a_wrong_citation_inside_a_quote_is_fixed_in_place() -> None:
    text = ('"the memory and computational complexity required to compute the attention '
            'matrix [chunk:2]. the QK matrix multiplication operation alone consumes '
            'N^2 time [chunk:2]" said.')
    out, fixes = verify_quotes(text, {1: C1, 2: C2})
    assert out == text.replace("matrix [chunk:2]", "matrix [chunk:1]", 1)
    assert [f["action"] for f in fixes] == ["moved"]


def test_a_quote_is_not_paired_with_a_citation_in_the_next_paragraph() -> None:
    text = '"the attention matrix is quadratic in the input sequence length"\n\nOther claim [chunk:2].'
    out, fixes = verify_quotes(text, {1: C1, 2: C2})
    assert out == text and fixes == []


def test_math_typed_as_latex_matches_the_pdf_text() -> None:
    text = ('"the $QK^\\top$ matrix multiplication operation alone consumes $N^2$ '
            'time and memory" [chunk:2].')
    pdf = "In particular, the QK⊤matrix multiplication operation\nalone consumes N2 time and memory."
    out, fixes = verify_quotes(text, {2: pdf})
    assert out == text and fixes == []


# Run 1263: chunk 85499 ends mid-sentence, then a page number and a
# footnote come before the sentence goes on in chunk 85500.
P1 = ("The computation costs of Transformers is derived from multiple factors. Firstly, "
      "the memory\nand computational complexity required to compute the attention matrix "
      "is quadratic in the\n4")
P2 = ("1. Memory restrictions tend to be applicable more to training and are generally of "
      "lesser impact on inference.\ninput sequence length, i.e., N ×N. In particular, the "
      "QK⊤matrix multiplication operation alone consumes N2 time and memory.")
SPLIT = ('"Firstly, the memory and computational complexity required to compute the '
         'attention matrix is quadratic in the input sequence length, i.e., $N \\times N$" ')


def test_a_quote_running_into_the_next_read_chunk_stays_cited() -> None:
    text = SPLIT + "[chunk:1]."
    out, fixes = verify_quotes(text, {1: P1, 2: P2})
    assert out == text and fixes == []


def test_a_quote_split_across_chunks_still_needs_the_cited_chunk() -> None:
    """The neighbour is only a continuation: a quote cited to a chunk that
    holds none of it moves to the chunk that holds all of it."""
    text = '"the QK matrix multiplication operation alone consumes N2 time" [chunk:1].'
    out, fixes = verify_quotes(text, {1: P1, 2: P2})
    assert out == text.replace("[chunk:1]", "[chunk:2]")


def test_two_far_apart_fragments_do_not_make_a_quote() -> None:
    far = "computational complexity " + "filler words here " * 40 + "time and memory"
    text = '"computational complexity time and memory" [chunk:1].'
    out, fixes = verify_quotes(text, {1: far})
    assert fixes and fixes[0]["action"] == "marked"


def test_short_and_cjk_quotes_are_not_checked() -> None:
    text = '使用 "ReLU" 路由 [chunk:1]；作者稱「這是一個翻譯過的句子不會逐字出現」[chunk:1]。'
    out, fixes = verify_quotes(text, {1: B})
    assert out == text and fixes == []


@pytest.mark.asyncio
async def test_loop_moves_a_misplaced_quote_citation(migrated_db: aiosqlite.Connection) -> None:
    _, _, tracer = await _setup(migrated_db)
    pid, ids = await _paper(migrated_db, "2401.0021", {"Results": [B, A]})
    await _enable(migrated_db, pid)
    wrong, right = ids[B], ids[A]
    quote = "excels in AP, making it suitable for tasks prioritizing detection speed"
    adapter = FakeAdapter([
        _calls(("read_chunks", {"ids": [wrong, right]})),
        _answer(f'YOLOv5s "{quote}" [chunk:{wrong}].'),
    ])
    events = await _drive(migrated_db, tracer, adapter, _read_registry())
    final = events[-1]
    assert isinstance(final, Final)
    assert final.text == f'YOLOv5s "{quote}" [chunk:{right}].'
    step = (await _rows(migrated_db, "agent:step"))[-1]["result"]
    assert step["quote_citations_fixed"] == [
        {"quote": quote, "cited": [wrong], "action": "moved", "to": [right]}]
