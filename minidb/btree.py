"""A B+ tree used for primary-key and secondary indexes.

* Every key lives in a leaf; internal nodes only hold routing keys.
* Leaves are linked left-to-right, so range scans walk the leaf chain
  instead of re-descending the tree.
* Each key maps to a set of row ids, so the tree supports non-unique
  (secondary) indexes as well as unique ones.
* Deletion is lazy: a key is removed from its leaf, but under-full nodes are
  not merged. Routing keys stay valid, so lookups remain correct; the cost is
  some wasted space after heavy deletes, which is the same trade-off several
  production engines make.

Lookups, inserts and deletes are O(log n). A range scan is O(log n + k)
for k matching keys.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Any, Iterator


class _Leaf:
    __slots__ = ("keys", "values", "next")

    def __init__(self) -> None:
        self.keys: list[Any] = []
        self.values: list[set[int]] = []
        self.next: _Leaf | None = None


class _Internal:
    __slots__ = ("keys", "children")

    def __init__(self, keys: list[Any], children: list[Any]) -> None:
        self.keys = keys
        self.children = children


class BPlusTree:
    def __init__(self, order: int = 32) -> None:
        if order < 3:
            raise ValueError("order must be at least 3")
        self.order = order  # maximum number of children per internal node
        self.max_keys = order - 1
        self.root: _Leaf | _Internal = _Leaf()
        self._key_count = 0

    def __len__(self) -> int:
        """Number of distinct keys in the tree."""
        return self._key_count

    # -------------------------------------------------------------- search

    def _find_leaf(self, key: Any) -> _Leaf:
        node = self.root
        while isinstance(node, _Internal):
            node = node.children[bisect_right(node.keys, key)]
        return node

    def _leftmost_leaf(self) -> _Leaf:
        node = self.root
        while isinstance(node, _Internal):
            node = node.children[0]
        return node

    def search(self, key: Any) -> list[int]:
        """Return the sorted row ids stored under ``key``."""
        leaf = self._find_leaf(key)
        i = bisect_left(leaf.keys, key)
        if i < len(leaf.keys) and leaf.keys[i] == key:
            return sorted(leaf.values[i])
        return []

    def __contains__(self, key: Any) -> bool:
        return bool(self.search(key))

    def range(
        self,
        low: Any = None,
        high: Any = None,
        low_inclusive: bool = True,
        high_inclusive: bool = True,
    ) -> Iterator[tuple[Any, list[int]]]:
        """Yield ``(key, row_ids)`` pairs with low <= key <= high, in key order.

        ``None`` for either bound means unbounded on that side.
        """
        leaf: _Leaf | None = self._leftmost_leaf() if low is None else self._find_leaf(low)
        while leaf is not None:
            for key, rows in zip(leaf.keys, leaf.values):
                if low is not None and (key < low or (key == low and not low_inclusive)):
                    continue
                if high is not None and (key > high or (key == high and not high_inclusive)):
                    return
                yield key, sorted(rows)
            leaf = leaf.next

    def items(self) -> Iterator[tuple[Any, list[int]]]:
        return self.range()

    # -------------------------------------------------------------- insert

    def insert(self, key: Any, row_id: int) -> None:
        split = self._insert(self.root, key, row_id)
        if split is not None:
            separator, right = split
            self.root = _Internal([separator], [self.root, right])

    def _insert(self, node, key, row_id):
        """Insert below ``node``; return (separator, new_right_node) if it split."""
        if isinstance(node, _Leaf):
            i = bisect_left(node.keys, key)
            if i < len(node.keys) and node.keys[i] == key:
                node.values[i].add(row_id)
                return None
            node.keys.insert(i, key)
            node.values.insert(i, {row_id})
            self._key_count += 1
            if len(node.keys) > self.max_keys:
                return self._split_leaf(node)
            return None

        i = bisect_right(node.keys, key)
        split = self._insert(node.children[i], key, row_id)
        if split is None:
            return None
        separator, right = split
        node.keys.insert(i, separator)
        node.children.insert(i + 1, right)
        if len(node.children) > self.order:
            return self._split_internal(node)
        return None

    @staticmethod
    def _split_leaf(leaf: _Leaf):
        mid = len(leaf.keys) // 2
        right = _Leaf()
        right.keys, leaf.keys = leaf.keys[mid:], leaf.keys[:mid]
        right.values, leaf.values = leaf.values[mid:], leaf.values[:mid]
        right.next, leaf.next = leaf.next, right
        # The separator is copied up: it still lives in the right leaf.
        return right.keys[0], right

    @staticmethod
    def _split_internal(node: _Internal):
        mid = len(node.keys) // 2
        separator = node.keys[mid]
        right = _Internal(node.keys[mid + 1:], node.children[mid + 1:])
        node.keys, node.children = node.keys[:mid], node.children[:mid + 1]
        # The separator is pushed up: it leaves this level entirely.
        return separator, right

    # -------------------------------------------------------------- delete

    def delete(self, key: Any, row_id: int) -> bool:
        """Remove one row id from ``key``. Returns True if something was removed."""
        leaf = self._find_leaf(key)
        i = bisect_left(leaf.keys, key)
        if i >= len(leaf.keys) or leaf.keys[i] != key or row_id not in leaf.values[i]:
            return False
        leaf.values[i].discard(row_id)
        if not leaf.values[i]:
            del leaf.keys[i]
            del leaf.values[i]
            self._key_count -= 1
        return True

    # --------------------------------------------------------- diagnostics

    def height(self) -> int:
        h, node = 1, self.root
        while isinstance(node, _Internal):
            h += 1
            node = node.children[0]
        return h

    def check_invariants(self) -> None:
        """Raise AssertionError if the tree structure is invalid (used by tests)."""
        leaf_depths: set[int] = set()

        def visit(node, low, high, depth):
            if isinstance(node, _Leaf):
                leaf_depths.add(depth)
                assert node.keys == sorted(node.keys), "leaf keys out of order"
                assert len(node.keys) <= self.max_keys, "leaf overflow"
                for k in node.keys:
                    assert low is None or k >= low, "key below routing bound"
                    assert high is None or k < high, "key above routing bound"
                return
            assert node.keys == sorted(node.keys), "internal keys out of order"
            assert len(node.children) == len(node.keys) + 1, "child count mismatch"
            assert len(node.children) <= self.order, "internal overflow"
            bounds = [low, *node.keys, high]
            for child, lo, hi in zip(node.children, bounds, bounds[1:]):
                visit(child, lo, hi, depth + 1)

        visit(self.root, None, None, 1)
        assert len(leaf_depths) == 1, "leaves are not all at the same depth"

        chained = [k for k, _ in self.items()]
        assert chained == sorted(chained), "leaf chain is not in order"
        assert len(chained) == self._key_count, "key count drifted"
