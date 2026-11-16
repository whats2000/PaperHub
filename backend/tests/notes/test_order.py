import random

from paperhub.notes.order import key_between


def test_first_key():
    assert key_between(None, None) == "a0"


def test_between_two_keys():
    k = key_between("a0", "a1")
    assert "a0" < k < "a1"


def test_after_and_before():
    assert key_between("a0", None) > "a0"
    assert key_between("az", None) > "az"
    assert key_between(None, "a0") < "a0"


def test_key_between_orders():
    rng = random.Random(7)
    keys = [key_between(None, None)]
    for _ in range(100):
        i = rng.randint(0, len(keys))
        a = keys[i - 1] if i > 0 else None
        b = keys[i] if i < len(keys) else None
        k = key_between(a, b)
        assert (a is None or a < k) and (b is None or k < b)
        keys.insert(i, k)
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)


def test_repeated_prepend_and_append_stay_ordered():
    first = key_between(None, None)
    head = tail = first
    for _ in range(60):
        k = key_between(None, head)
        assert k < head
        head = k
        k = key_between(tail, None)
        assert k > tail
        tail = k
    assert head < first < tail
