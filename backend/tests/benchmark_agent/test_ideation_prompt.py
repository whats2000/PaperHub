"""Plan H3 Task 5: the shipped ideation prompt is the eval winner and its
examples do not leak eval answers."""
from __future__ import annotations

from pathlib import Path

from benchmark.agent.prompts import load_variant
from paperhub.ideation.agent import PROPOSE_SLOT
from paperhub.llm.prompts.registry import PromptRegistry

BACKEND = Path(__file__).resolve().parents[2]
PROMPTS = BACKEND / "benchmark/agent/prompts"
WINNER = "v18"  # see benchmark/agent/prompts/ideation/README.md (round 8)
VARIABLES = ("{problem}", "{tried}", "{cards_xml}", "{rejected_xml}", "{mode}")


def test_shipped_prompt_matches_the_eval_winner() -> None:
    assert PROPOSE_SLOT == "ideation_propose/v3"
    slot = PromptRegistry().get(PROPOSE_SLOT)
    assert (slot.system, slot.user_template) == load_variant(
        "ideation", WINNER, prompts_dir=PROMPTS)


def test_every_variant_renders_the_five_variables() -> None:
    for path in sorted((PROMPTS / "ideation").glob("v*.yaml")):
        _system, user = load_variant("ideation", path.stem, prompts_dir=PROMPTS)
        for v in VARIABLES:
            assert v in user, (path.name, v)
        # Round 8 variables (v16+): extra keys are ignored by older variants.
        user.format(problem="p", tried="t", cards_xml="c", rejected_xml="r", mode="problem",
                    existing_xml="e", response_language="")


def test_examples_avoid_the_eval_mechanisms() -> None:
    """The in-prompt examples must not teach an eval answer (masked prediction,
    few-step sampling, anchors, subword merges, in-context prompting …)."""
    for path in sorted((PROMPTS / "ideation").glob("v*.yaml")):
        text = path.read_text(encoding="utf-8").casefold()
        for probe in ("mask", "anchor", "few-shot", "in-context", "subword", "state space",
                      "expert", "denois", "shortcut", "contrastive"):
            assert probe not in text, (path.name, probe)


def test_round8_variants_render_the_new_variables() -> None:
    for version in ("v16", "v17"):
        _system, user = load_variant("ideation", version, prompts_dir=PROMPTS)
        for v in ("{existing_xml}", "{response_language}"):
            assert v in user, (version, v)
