from paperhub.translation.placeholders import (
    br_position_intact,
    normalize_language,
    placeholder_ids,
    placeholders_intact,
    source_hash,
)


def test_placeholder_ids_ordered() -> None:
    assert placeholder_ids("a ⟦1⟧ b ⟦BR⟧ c ⟦0⟧") == ["1", "BR", "0"]
    assert placeholder_ids("no tokens") == []


def test_placeholders_intact() -> None:
    assert placeholders_intact("a ⟦0⟧ b ⟦1⟧", "x ⟦1⟧ y ⟦0⟧")  # order may change
    assert placeholders_intact("plain", "純文字")
    assert not placeholders_intact("a ⟦0⟧ ⟦1⟧", "x ⟦0⟧")  # dropped
    assert not placeholders_intact("a ⟦0⟧", "x ⟦0⟧ ⟦0⟧")  # duplicated
    assert not placeholders_intact("a ⟦0⟧", "x ⟦0⟧ ⟦7⟧")  # invented
    assert placeholders_intact("a ⟦BR⟧ b", "x ⟦BR⟧ y")
    assert not placeholders_intact("a ⟦BR⟧ b", "x y")


def test_normalize_language() -> None:
    assert normalize_language("  繁體中文（香港）  ") == "繁體中文（香港）"
    assert normalize_language("Traditional   \t Chinese") == "Traditional Chinese"


def test_source_hash_is_sha256_hex() -> None:
    assert source_hash("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_br_position_intact_accepts_a_mid_sentence_break() -> None:
    src = "We clip the norm at ⟦0⟧ and ⟦BR⟧ apply weight decay ⟦1⟧."
    assert br_position_intact(src, "我們將範數裁剪在 ⟦0⟧，並 ⟦BR⟧ 套用權重衰減 ⟦1⟧。")


def test_br_position_intact_rejects_a_break_moved_to_the_end() -> None:
    src = "We clip the norm at ⟦0⟧ and ⟦BR⟧ apply weight decay ⟦1⟧."
    assert not br_position_intact(src, "我們將範數裁剪在 ⟦0⟧，並套用權重衰減 ⟦1⟧。⟦BR⟧")
    assert not br_position_intact(src, "我們將範數裁剪在 ⟦0⟧，並套用權重衰減 ⟦1⟧ ⟦BR⟧。")


def test_br_position_intact_rejects_a_break_moved_to_the_start() -> None:
    src = "We clip the norm and ⟦BR⟧ apply weight decay."
    assert not br_position_intact(src, "⟦BR⟧ 我們裁剪範數並套用權重衰減。")


def test_br_position_intact_allows_edge_breaks_that_are_in_the_source() -> None:
    assert br_position_intact("Weight decay ⟦0⟧. ⟦BR⟧", "權重衰減 ⟦0⟧。⟦BR⟧")
    assert br_position_intact("⟦BR⟧ Weight decay.", "⟦BR⟧ 權重衰減。")
    assert br_position_intact("No break here.", "這裡沒有分頁。")
