# minidb

A small relational database engine written from scratch in Python, with no dependencies. It has a hand-written SQL tokenizer and recursive-descent parser, B+ tree indexes, a rule-based query planner, SQL three-valued logic, and crash-safe persistence to disk.

I built it to understand what happens inside the databases I use every day (PostgreSQL through Supabase), not just how to call them.

```
$ python -m minidb ./data
minidb> CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER);
minidb> INSERT INTO users VALUES (1, 'ada', 36), (2, 'alan', 41), (3, 'grace', 85);
minidb> CREATE INDEX idx_age ON users (age);
minidb> EXPLAIN SELECT name FROM users WHERE age >= 35 AND age < 50;
+-------------------------------------------+
| plan                                      |
+-------------------------------------------+
| INDEX RANGE SCAN users.age in [35, 50)    |
+-------------------------------------------+
minidb> SELECT name FROM users WHERE age >= 35 AND age < 50 ORDER BY name;
+------+
| name |
+------+
| ada  |
| alan |
+------+
```

## Architecture

```
SQL text ──► tokenizer ──► parser ──► AST ──► planner ──► executor ──► Result
                                                 │            │
                                                 ▼            ▼
                                            B+ tree      table storage ──► JSON on disk
                                            indexes      (row id → tuple)  (atomic writes)
```

| Module | Responsibility |
|---|---|
| `tokenizer.py` | Lexical analysis: keywords, identifiers, numbers, `'escaped ''strings'''`, operators and comments. Errors report the character position. |
| `parser.py` | Recursive-descent parser with a precedence-climbing expression grammar (`OR` < `AND` < `NOT` < comparison < `+ -` < `* /` < unary). |
| `ast.py` | Immutable dataclasses for every statement and expression node. |
| `btree.py` | B+ tree with linked leaves, non-unique keys and range scans. |
| `engine.py` | Typed storage, expression evaluation, query planning, constraints and persistence. |
| `__main__.py` | Interactive shell with `.tables` and `.schema` commands. |

## Supported SQL

```sql
CREATE TABLE name (col INTEGER PRIMARY KEY, col TEXT, col REAL);
CREATE INDEX idx ON name (col);
DROP TABLE name;

INSERT INTO name [(cols)] VALUES (...), (...);
SELECT * | col, ... | COUNT(*) FROM name
    [WHERE expr] [ORDER BY col [ASC|DESC]] [LIMIT n];
UPDATE name SET col = expr, ... [WHERE expr];
DELETE FROM name [WHERE expr];
EXPLAIN SELECT ...;
```

Expressions support `= != <> < <= > >=`, `AND OR NOT`, `IS [NOT] NULL`, `+ - * /`, parentheses, and `TRUE`, `FALSE` and `NULL` literals.

## Design decisions

**B+ tree indexes.** Every key lives in a leaf and the leaves form a linked list, so a range query descends once (O(log n)) and then walks sideways (O(k)). Each key maps to a set of row ids, so one structure serves both unique primary keys and non-unique secondary indexes. Deletion is lazy: under-full nodes aren't merged, but routing keys stay valid, so lookups stay correct. The tests check the structural invariants (sorted keys, equal leaf depth, routing bounds, node capacity) after thousands of random operations.

**Query planning.** The planner splits the `WHERE` clause into its top-level `AND` predicates and looks for `column <op> literal` on an indexed column:

- an equality becomes an **index lookup**;
- comparisons on the same column combine into the tightest **index range scan**, so `age > 30 AND age >= 35 AND age < 50` becomes `[35, 50)`;
- `literal < column` is flipped to `column > literal`;
- `OR`, functions of columns (`id + 0 = 5`) and unindexed columns fall back to a **full table scan**.

The executor always re-checks the full `WHERE` clause against each candidate row, so a plan only needs to return a superset of the matches. That keeps the planner simple, and a planning mistake can cost speed but can never drop a row.

**SQL three-valued logic.** Comparisons with `NULL` evaluate to unknown, `NOT unknown` is unknown, and `WHERE` keeps only rows that are exactly true, matching the SQL standard. `NULL`s sort first ascending, as in SQLite.

**Atomic statements.** A multi-row `INSERT` or `UPDATE` validates types and primary-key uniqueness for every row before writing any of them, so a failing statement leaves the table untouched. `UPDATE t SET id = id + 1` works because the uniqueness check knows which keys are being rewritten in the same statement.

**Crash-safe persistence.** Each table is written to a temporary file and then swapped in with `os.replace`, which is atomic. A crash mid-write leaves the previous version intact rather than a half-written file. Indexes are rebuilt on load.

## Testing

```bash
pip install pytest
pytest
```

There are 42 tests, including:

- **a differential test against SQLite**, which generates 300 random rows (including `NULL`s) and 300 random nested `WHERE` clauses with `AND`, `OR`, `NOT` and `IS NULL`, and checks that minidb returns exactly the same rows as SQLite;
- **B+ tree property tests**: random inserts and deletes at several tree orders, checked against a dictionary reference model;
- planner tests that assert the chosen plan through `EXPLAIN`;
- constraint, atomicity, type-checking and persistence round-trip tests.

## Benchmark: index lookup vs full scan

`python -m benchmarks.index_vs_scan`

| Rows | Index lookup | Full scan | Speed-up |
|---:|---:|---:|---:|
| 1,000 | 0.031 ms | 1.5 ms | ~49× |
| 10,000 | 0.033 ms | 15.5 ms | ~470× |
| 50,000 | 0.031 ms | 77.0 ms | ~2,460× |

The lookup time stays flat as the table grows (O(log n)), while the scan grows linearly (O(n)).

## Limitations and next steps

- No joins, aggregates beyond `COUNT(*)`, or `GROUP BY`. Joins (nested-loop, then index nested-loop) are the natural next step.
- Persistence rewrites a whole table per statement. A page-based storage layer with a write-ahead log would be the next stage.
- No concurrent access or transactions spanning multiple statements.

## Licence

MIT
