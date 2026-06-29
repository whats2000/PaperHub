import pytest

from benchmark.agent import prompts


def _write(d, stage, version, system, user):
    p = d / stage
    p.mkdir(parents=True, exist_ok=True)
    (p / f"{version}.yaml").write_text(f"system: |\n  {system}\nuser: |\n  {user}\n", encoding="utf-8")


def test_load_variant(tmp_path):
    _write(tmp_path, "router", "v1", "You classify intent.", "MSG: {user_message}")
    system, user = prompts.load_variant("router", "v1", prompts_dir=tmp_path)
    assert "classify intent" in system
    assert user.strip() == "MSG: {user_message}"


def test_list_variants(tmp_path):
    _write(tmp_path, "router", "v1", "a", "b")
    _write(tmp_path, "router", "v2", "a", "b")
    assert prompts.list_variants("router", prompts_dir=tmp_path) == ["v1", "v2"]


def test_missing_variant_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        prompts.load_variant("router", "v9", prompts_dir=tmp_path)
