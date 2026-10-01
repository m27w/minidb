"""Storage, query planning and execution."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import ast
from .btree import BPlusTree
from .parser import parse


class ExecutionError(Exception):
    """Raised for valid SQL that cannot be executed (missing table, type error, ...)."""


@dataclass
class Result:
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    message: str = ""
    plan: str = ""

    def __iter__(self):
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)


# ===================================================================== values

def coerce(value: Any, column_type: str, column_name: str) -> Any:
    """Convert a Python value to the column's declared type, or raise."""
    if value is None:
        return None
    if isinstance(value, bool):
        # Store booleans the way SQLite does: as integers.
        value = int(value)
    if column_type == "INTEGER":
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
    elif column_type == "REAL":
        if isinstance(value, (int, float)):
            return float(value)
    elif column_type == "TEXT":
        if isinstance(value, str):
            return value
    raise ExecutionError(f"Cannot store {value!r} in {column_type} column '{column_name}'")


def _comparable(a: Any, b: Any) -> bool:
    numeric = (int, float)
    return (isinstance(a, numeric) and isinstance(b, numeric)) or type(a) is type(b)


def evaluate(expr: ast.Expr, row: dict[str, Any]) -> Any:
    """Evaluate an expression against a row using SQL three-valued logic.

    Comparisons involving NULL produce NULL (None), and only rows whose WHERE
    clause evaluates to exactly True are returned.
    """
    if isinstance(expr, ast.Literal):
        return expr.value

    if isinstance(expr, ast.Column):
        if expr.name not in row:
            raise ExecutionError(f"Unknown column '{expr.name}'")
        return row[expr.name]

    if isinstance(expr, ast.IsNull):
        is_null = evaluate(expr.operand, row) is None
        return not is_null if expr.negated else is_null

    if isinstance(expr, ast.UnaryOp):
        value = evaluate(expr.operand, row)
        if value is None:
            return None
        if expr.op == "NOT":
            return not value
        if expr.op == "-":
            if not isinstance(value, (int, float)):
                raise ExecutionError("Unary minus needs a number")
            return -value
        raise ExecutionError(f"Unknown operator {expr.op}")

    if isinstance(expr, ast.BinaryOp):
        op = expr.op
        if op == "AND":
            left = evaluate(expr.left, row)
            if left is False:
                return False
            right = evaluate(expr.right, row)
            if right is False:
                return False
            if left is None or right is None:
                return None
            return True
        if op == "OR":
            left = evaluate(expr.left, row)
            if left is True:
                return True
            right = evaluate(expr.right, row)
            if right is True:
                return True
            if left is None or right is None:
                return None
            return False

        left, right = evaluate(expr.left, row), evaluate(expr.right, row)
        if left is None or right is None:
            return None
        if not _comparable(left, right):
            raise ExecutionError(f"Cannot apply '{op}' to {left!r} and {right!r}")

        if op == "=":
            return left == right
        if op == "!=":
            return left != right
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            if right == 0:
                raise ExecutionError("Division by zero")
            if isinstance(left, int) and isinstance(right, int):
                return left // right if left * right >= 0 else -(-left // right)
            return left / right
        raise ExecutionError(f"Unknown operator {op}")

    raise ExecutionError(f"Cannot evaluate {expr!r}")


# ===================================================================== tables

@dataclass
class ColumnSchema:
    name: str
    type: str
    primary_key: bool = False


class Table:
    def __init__(self, name: str, columns: list[ColumnSchema]):
        self.name = name
        self.columns = columns
        self.column_map = {c.name: c for c in columns}
        self.rows: dict[int, tuple] = {}
        self.next_row_id = 1
        self.indexes: dict[str, str] = {}          # index name -> column
        self.trees: dict[str, BPlusTree] = {}      # column -> tree
        self.primary_key = next((c.name for c in columns if c.primary_key), None)
        if self.primary_key:
            self.add_index(f"pk_{name}", self.primary_key)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def as_dict(self, values: tuple) -> dict[str, Any]:
        return dict(zip(self.column_names, values))

    def add_index(self, index_name: str, column: str) -> None:
        if column not in self.column_map:
            raise ExecutionError(f"No column '{column}' in table '{self.name}'")
        if index_name in self.indexes:
            raise ExecutionError(f"Index '{index_name}' already exists")
        if column in self.trees:
            raise ExecutionError(f"Column '{column}' is already indexed")
        tree = BPlusTree()
        position = self.column_names.index(column)
        for row_id, values in self.rows.items():
            if values[position] is not None:
                tree.insert(values[position], row_id)
        self.indexes[index_name] = column
        self.trees[column] = tree

    def _index_add(self, row_id: int, values: tuple) -> None:
        for column, tree in self.trees.items():
            value = values[self.column_names.index(column)]
            if value is not None:
                tree.insert(value, row_id)

    def _index_remove(self, row_id: int, values: tuple) -> None:
        for column, tree in self.trees.items():
            value = values[self.column_names.index(column)]
            if value is not None:
                tree.delete(value, row_id)

    def insert(self, values: tuple) -> int:
        row_id = self.next_row_id
        self.next_row_id += 1
        self.rows[row_id] = values
        self._index_add(row_id, values)
        return row_id

    def replace(self, row_id: int, values: tuple) -> None:
        self._index_remove(row_id, self.rows[row_id])
        self.rows[row_id] = values
        self._index_add(row_id, values)

    def remove(self, row_id: int) -> None:
        self._index_remove(row_id, self.rows.pop(row_id))

    # ------------------------------------------------------- persistence

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "columns": [vars(c) for c in self.columns],
            "indexes": {k: v for k, v in self.indexes.items() if k != f"pk_{self.name}"},
            "next_row_id": self.next_row_id,
            "rows": [[row_id, list(values)] for row_id, values in self.rows.items()],
        }

    @classmethod
    def from_json(cls, data: dict) -> "Table":
        table = cls(data["name"], [ColumnSchema(**c) for c in data["columns"]])
        for row_id, values in data["rows"]:
            table.rows[row_id] = tuple(values)
            table._index_add(row_id, tuple(values))
        table.next_row_id = data["next_row_id"]
        for index_name, column in data["indexes"].items():
            table.add_index(index_name, column)
        return table


# =================================================================== planner

@dataclass
class Plan:
    kind: str                     # "full_scan", "index_lookup" or "index_range"
    column: str | None = None
    key: Any = None
    low: Any = None
    high: Any = None
    low_inclusive: bool = True
    high_inclusive: bool = True

    def describe(self, table: str) -> str:
        if self.kind == "index_lookup":
            return f"INDEX LOOKUP {table}.{self.column} = {self.key!r}"
        if self.kind == "index_range":
            lo = "-inf" if self.low is None else repr(self.low)
            hi = "+inf" if self.high is None else repr(self.high)
            lb = "[" if self.low_inclusive else "("
            rb = "]" if self.high_inclusive else ")"
            return f"INDEX RANGE SCAN {table}.{self.column} in {lb}{lo}, {hi}{rb}"
        return f"FULL TABLE SCAN {table}"


FLIPPED = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "=": "="}


def _conjuncts(expr: ast.Expr | None) -> list[ast.Expr]:
    if expr is None:
        return []
    if isinstance(expr, ast.BinaryOp) and expr.op == "AND":
        return _conjuncts(expr.left) + _conjuncts(expr.right)
    return [expr]


def choose_plan(table: Table, where: ast.Expr | None) -> Plan:
    """Pick the cheapest access path for a WHERE clause.

    Only top-level AND-ed predicates of the form ``column <op> literal`` on an
    indexed column are considered. Equality beats ranges; anything else falls
    back to a full scan. The full WHERE clause is always re-checked against
    each fetched row, so the plan only has to return a superset of matches.
    """
    ranges: dict[str, Plan] = {}

    for predicate in _conjuncts(where):
        if not isinstance(predicate, ast.BinaryOp) or predicate.op not in FLIPPED:
            continue
        left, right, op = predicate.left, predicate.right, predicate.op
        if isinstance(left, ast.Literal) and isinstance(right, ast.Column):
            left, right, op = right, left, FLIPPED[op]
        if not (isinstance(left, ast.Column) and isinstance(right, ast.Literal)):
            continue
        if left.name not in table.trees or right.value is None:
            continue
        column = table.column_map[left.name]
        try:
            key = coerce(right.value, column.type, column.name)
        except ExecutionError:
            continue  # incompatible literal: let the evaluator report it

        if op == "=":
            return Plan("index_lookup", left.name, key=key)

        plan = ranges.setdefault(left.name, Plan("index_range", left.name))
        if op in (">", ">="):
            if plan.low is None or key > plan.low or (key == plan.low and op == ">"):
                plan.low, plan.low_inclusive = key, op == ">="
        else:
            if plan.high is None or key < plan.high or (key == plan.high and op == "<"):
                plan.high, plan.high_inclusive = key, op == "<="

    if ranges:
        # Prefer a range bounded on both sides.
        return max(ranges.values(), key=lambda p: (p.low is not None) + (p.high is not None))
    return Plan("full_scan")


def candidate_row_ids(table: Table, plan: Plan) -> Iterable[int]:
    if plan.kind == "index_lookup":
        return table.trees[plan.column].search(plan.key)
    if plan.kind == "index_range":
        tree = table.trees[plan.column]
        ids: list[int] = []
        for _, row_ids in tree.range(plan.low, plan.high, plan.low_inclusive, plan.high_inclusive):
            ids.extend(row_ids)
        return ids
    return list(table.rows.keys())


# ================================================================== database

class Database:
    """An in-memory relational database with optional on-disk persistence.

    >>> db = Database()
    >>> _ = db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    >>> _ = db.execute("INSERT INTO t VALUES (1, 'ada'), (2, 'alan')")
    >>> db.execute("SELECT name FROM t WHERE id = 2").rows
    [('alan',)]
    """

    def __init__(self, path: str | os.PathLike | None = None):
        self.tables: dict[str, Table] = {}
        self.path = Path(path) if path is not None else None
        if self.path is not None:
            self.path.mkdir(parents=True, exist_ok=True)
            for file in sorted(self.path.glob("*.json")):
                table = Table.from_json(json.loads(file.read_text()))
                self.tables[table.name] = table

    # ----------------------------------------------------------- public

    def execute(self, sql: str) -> Result:
        """Execute one or more statements and return the last result."""
        result = Result()
        for statement in parse(sql):
            result = self._execute(statement)
        return result

    # ---------------------------------------------------------- helpers

    def _table(self, name: str) -> Table:
        if name not in self.tables:
            raise ExecutionError(f"No such table '{name}'")
        return self.tables[name]

    def _save(self, table_name: str) -> None:
        if self.path is None:
            return
        target = self.path / f"{table_name}.json"
        if table_name not in self.tables:
            target.unlink(missing_ok=True)
            return
        # Write to a temp file then atomically rename, so a crash mid-write
        # never leaves a half-written table on disk.
        fd, tmp = tempfile.mkstemp(dir=self.path, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(self.tables[table_name].to_json(), f)
        os.replace(tmp, target)

    @staticmethod
    def _matching(table: Table, where: ast.Expr | None, plan: Plan) -> list[int]:
        matches = []
        for row_id in candidate_row_ids(table, plan):
            if where is None or evaluate(where, table.as_dict(table.rows[row_id])) is True:
                matches.append(row_id)
        return matches

    def _check_primary_key(self, table: Table, writes: list[tuple[int | None, tuple]]) -> None:
        """Validate PRIMARY KEY uniqueness for a batch of rows about to be written.

        ``writes`` is a list of ``(row_id, values)`` pairs; ``row_id`` is None for
        brand-new rows, or the id of an existing row being rewritten (UPDATE).
        A key may be reused if the row that currently owns it is itself being
        rewritten in the same statement (e.g. ``UPDATE t SET id = id + 1``).
        """
        if not table.primary_key:
            return
        position = table.column_names.index(table.primary_key)
        tree = table.trees[table.primary_key]
        rewritten = {row_id for row_id, _ in writes if row_id is not None}
        seen: set[Any] = set()
        for _, values in writes:
            key = values[position]
            if key is None:
                raise ExecutionError(f"PRIMARY KEY '{table.primary_key}' cannot be NULL")
            if key in seen or set(tree.search(key)) - rewritten:
                raise ExecutionError(f"Duplicate PRIMARY KEY {key!r}")
            seen.add(key)

    # -------------------------------------------------------- statements

    def _execute(self, statement: ast.Statement) -> Result:
        if isinstance(statement, ast.CreateTable):
            if statement.table in self.tables:
                raise ExecutionError(f"Table '{statement.table}' already exists")
            columns = [ColumnSchema(c.name, c.type, c.primary_key) for c in statement.columns]
            self.tables[statement.table] = Table(statement.table, columns)
            self._save(statement.table)
            return Result(message=f"Created table {statement.table}")

        if isinstance(statement, ast.DropTable):
            self._table(statement.table)
            del self.tables[statement.table]
            self._save(statement.table)
            return Result(message=f"Dropped table {statement.table}")

        if isinstance(statement, ast.CreateIndex):
            table = self._table(statement.table)
            table.add_index(statement.name, statement.column)
            self._save(table.name)
            return Result(message=f"Created index {statement.name} on {table.name}({statement.column})")

        if isinstance(statement, ast.Insert):
            return self._insert(statement)
        if isinstance(statement, ast.Select):
            return self._select(statement)
        if isinstance(statement, ast.Update):
            return self._update(statement)
        if isinstance(statement, ast.Delete):
            return self._delete(statement)
        if isinstance(statement, ast.Explain):
            table = self._table(statement.statement.table)
            plan = choose_plan(table, statement.statement.where)
            description = plan.describe(table.name)
            return Result(["plan"], [(description,)], plan=description)
        raise ExecutionError(f"Unsupported statement {statement!r}")

    def _insert(self, statement: ast.Insert) -> Result:
        table = self._table(statement.table)
        target_columns = list(statement.columns) if statement.columns else table.column_names
        for name in target_columns:
            if name not in table.column_map:
                raise ExecutionError(f"No column '{name}' in table '{table.name}'")

        # Validate every row before writing any, so a bad row leaves the table untouched.
        prepared: list[tuple] = []
        for exprs in statement.rows:
            if len(exprs) != len(target_columns):
                raise ExecutionError(f"Expected {len(target_columns)} values, got {len(exprs)}")
            supplied = {name: evaluate(e, {}) for name, e in zip(target_columns, exprs)}
            prepared.append(tuple(
                coerce(supplied.get(c.name), c.type, c.name) for c in table.columns
            ))

        self._check_primary_key(table, [(None, values) for values in prepared])
        for values in prepared:
            table.insert(values)
        self._save(table.name)
        return Result(message=f"Inserted {len(prepared)} row(s)")

    def _select(self, statement: ast.Select) -> Result:
        table = self._table(statement.table)
        plan = choose_plan(table, statement.where)
        row_ids = self._matching(table, statement.where, plan)
        description = plan.describe(table.name)

        if statement.count_star:
            return Result(["count"], [(len(row_ids),)], plan=description)

        rows = [table.rows[i] for i in row_ids]

        if statement.order_by is not None:
            name = statement.order_by.column
            if name not in table.column_map:
                raise ExecutionError(f"Unknown column '{name}'")
            position = table.column_names.index(name)
            # NULLs sort first ascending (and therefore last descending), like SQLite.
            rows.sort(
                key=lambda r: (r[position] is not None, r[position] if r[position] is not None else 0),
                reverse=statement.order_by.descending,
            )

        if statement.limit is not None:
            rows = rows[: statement.limit]

        if statement.columns is None:
            return Result(table.column_names, rows, plan=description)

        positions = []
        for name in statement.columns:
            if name not in table.column_map:
                raise ExecutionError(f"Unknown column '{name}'")
            positions.append(table.column_names.index(name))
        projected = [tuple(r[p] for p in positions) for r in rows]
        return Result(list(statement.columns), projected, plan=description)

    def _update(self, statement: ast.Update) -> Result:
        table = self._table(statement.table)
        for name, _ in statement.assignments:
            if name not in table.column_map:
                raise ExecutionError(f"Unknown column '{name}'")

        plan = choose_plan(table, statement.where)
        row_ids = self._matching(table, statement.where, plan)

        # Compute every new row from the *old* values first (SQL semantics),
        # validate, and only then write.
        new_rows: dict[int, tuple] = {}
        for row_id in row_ids:
            current = table.as_dict(table.rows[row_id])
            updated = dict(current)
            for name, expr in statement.assignments:
                column = table.column_map[name]
                updated[name] = coerce(evaluate(expr, current), column.type, name)
            new_rows[row_id] = tuple(updated[c] for c in table.column_names)

        self._check_primary_key(table, list(new_rows.items()))
        for row_id, values in new_rows.items():
            table.replace(row_id, values)
        self._save(table.name)
        return Result(message=f"Updated {len(new_rows)} row(s)", plan=plan.describe(table.name))

    def _delete(self, statement: ast.Delete) -> Result:
        table = self._table(statement.table)
        plan = choose_plan(table, statement.where)
        row_ids = self._matching(table, statement.where, plan)
        for row_id in row_ids:
            table.remove(row_id)
        self._save(table.name)
        return Result(message=f"Deleted {len(row_ids)} row(s)", plan=plan.describe(table.name))

