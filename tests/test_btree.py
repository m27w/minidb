import random

import pytest

from minidb.btree import BPlusTree


@pytest.mark.parametrize("order", [3, 4, 5, 32])
def test_random_inserts_keep_invariants(order):
    rng = random.Random(order)
    tree = BPlusTree(order=order)
    keys = rng.sample(range(10_000), 2_000)
    for row_id, key in enumerate(keys):
        tree.insert(key, row_id)
    tree.check_invariants()
    assert len(tree) == len(keys)
    assert [k for k, _ in tree.items()] == sorted(keys)
    for row_id, key in enumerate(keys):
        assert tree.search(key) == [row_id]
    assert tree.height() > 1


def test_duplicate_keys_collect_row_ids():
    tree = BPlusTree(order=4)
    for row_id in range(10):
        tree.insert("same", row_id)
    assert tree.search("same") == list(range(10))
    assert len(tree) == 1


def test_range_bounds():
    tree = BPlusTree(order=4)
    for i in range(100):
        tree.insert(i, i)
    keys = lambda **kw: [k for k, _ in tree.range(**kw)]
    assert keys(low=10, high=15) == [10, 11, 12, 13, 14, 15]
    assert keys(low=10, high=15, low_inclusive=False, high_inclusive=False) == [11, 12, 13, 14]
    assert keys(high=3) == [0, 1, 2, 3]
    assert keys(low=97) == [97, 98, 99]
    assert keys(low=200) == []


def test_delete_against_reference_model():
    rng = random.Random(42)
    tree = BPlusTree(order=4)
    reference: dict[int, set[int]] = {}
    for step in range(5_000):
        key, row_id = rng.randrange(300), rng.randrange(5)
        if rng.random() < 0.6:
            tree.insert(key, row_id)
            reference.setdefault(key, set()).add(row_id)
        else:
            removed = tree.delete(key, row_id)
            expected = row_id in reference.get(key, set())
            assert removed == expected
            if expected:
                reference[key].discard(row_id)
                if not reference[key]:
                    del reference[key]
    tree.check_invariants()
    assert dict((k, set(v)) for k, v in tree.items()) == reference


def test_order_must_be_at_least_three():
    with pytest.raises(ValueError):
        BPlusTree(order=2)
