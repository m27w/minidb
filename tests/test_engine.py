import random
import sqlite3

import pytest

from minidb import Database, ExecutionError


@pytest.fixture
def db():
    database = Database()
    database.execute(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER, score REAL);
        INSERT INTO users VALUES
            (1, 'ada', 36, 91.5),
            (2, 'alan', 41, 88.0),
            (3, 'grace', 85, NULL),
            (4, 'linus', NULL, 72.25),
            (5, 'margaret', 33, 99.0);
        """
    )
    return database


def test_select_projection_order_and_limit(db):
    result = db.execute("SELECT name, age FROM users WHERE age > 34 ORDER BY age DESC LIMIT 2")
    assert result.columns == ["name", "age"]
    assert result.rows == [("grace", 85), ("alan", 41)]


def test_null_semantics(db):
    # A comparison with NULL is unknown, so linus is excluded from both sides.
    assert len(db.execute("SELECT * FROM users WHERE age > 0")) == 4
    assert len(db.execute("SELECT * FROM users WHERE NOT age > 0")) == 0
    assert db.execute("SELECT name FROM users WHERE age IS NULL").rows == [("linus",)]
    # NULLs sort first in ascending order.
    assert db.execute("SELECT name FROM users ORDER BY score LIMIT 1").rows == [("grace",)]


def test_arithmetic_in_where_and_update(db):
    assert db.execute("SELECT name FROM users WHERE age * 2 = 82").rows == [("alan",)]
    db.execute("UPDATE users SET age = age + 1 WHERE name = 'ada'")
    assert db.execute("SELECT age FROM users WHERE id = 1").rows == [(37,)]


def test_count_star(db):
    assert db.execute("SELECT COUNT(*) FROM users WHERE score >= 90").rows == [(2,)]


def test_primary_key_lookup_uses_index(db):
    result = db.execute("SELECT name FROM users WHERE id = 3")
    assert result.rows == [("grace",)]
    assert result.plan == "INDEX LOOKUP users.id = 3"


def test_secondary_index_and_range_plan(db):
    db.execute("CREATE INDEX idx_age ON users (age)")
    plan = db.execute("EXPLAIN SELECT * FROM users WHERE age >= 35 AND age < 50 AND name != 'x'").rows
    assert plan == [("INDEX RANGE SCAN users.age in [35, 50)",)]
    result = db.execute("SELECT name FROM users WHERE age >= 35 AND age < 50 ORDER BY name")
    assert result.rows == [("ada",), ("alan",)]


def test_literal_on_left_is_flipped_for_planning(db):
    plan = db.execute("EXPLAIN SELECT * FROM users WHERE 3 < id").rows[0][0]
    assert plan == "INDEX RANGE SCAN users.id in (3, +inf]"


def test_or_falls_back_to_full_scan(db):
    plan = db.execute("EXPLAIN SELECT * FROM users WHERE id = 1 OR id = 2").rows[0][0]
    assert plan == "FULL TABLE SCAN users"
    assert len(db.execute("SELECT * FROM users WHERE id = 1 OR id = 2")) == 2


def test_index_stays_correct_after_update_and_delete(db):
    db.execute("CREATE INDEX idx_name ON users (name)")
    db.execute("UPDATE users SET name = 'ada lovelace' WHERE id = 1")
    db.execute("DELETE FROM users WHERE name = 'alan'")
    assert db.execute("SELECT id FROM users WHERE name = 'ada'").rows == []
    assert db.execute("SELECT id FROM users WHERE name = 'ada lovelace'").rows == [(1,)]
    assert db.execute("SELECT COUNT(*) FROM users").rows == [(4,)]


def test_primary_key_constraints(db):
    with pytest.raises(ExecutionError, match="Duplicate"):
        db.execute("INSERT INTO users VALUES (1, 'dupe', 1, 1.0)")
    with pytest.raises(ExecutionError, match="cannot be NULL"):
        db.execute("INSERT INTO users (name) VALUES ('no id')")
    with pytest.raises(ExecutionError, match="Duplicate"):
        db.execute("UPDATE users SET id = 2 WHERE id = 1")
    # Shifting every key at once is fine: each old key is being rewritten too.
    db.execute("UPDATE users SET id = id + 10")
    assert db.execute("SELECT name FROM users WHERE id = 11").rows == [("ada",)]


def test_insert_is_atomic(db):
    with pytest.raises(ExecutionError):
        db.execute("INSERT INTO users VALUES (10, 'ok', 1, 1.0), (10, 'dupe', 2, 2.0)")
    assert db.execute("SELECT COUNT(*) FROM users").rows == [(5,)]


def test_type_checking(db):
    with pytest.raises(ExecutionError, match="Cannot store"):
        db.execute("INSERT INTO users VALUES (9, 'x', 'not a number', 1.0)")
    with pytest.raises(ExecutionError, match="Cannot apply"):
        db.execute("SELECT * FROM users WHERE name > 5")
    # Integers are widened into REAL columns.
    db.execute("INSERT INTO users VALUES (9, 'x', 20, 50)")
    assert db.execute("SELECT score FROM users WHERE id = 9").rows == [(50.0,)]


def test_unknown_table_and_column(db):
    with pytest.raises(ExecutionError, match="No such table"):
        db.execute("SELECT * FROM nope")
    with pytest.raises(ExecutionError, match="Unknown column"):
        db.execute("SELECT nope FROM users")


def test_persistence_round_trip(tmp_path):
    first = Database(tmp_path)
    first.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    first.execute("CREATE INDEX idx_body ON notes (body)")
    first.execute("INSERT INTO notes VALUES (1, 'hello'), (2, 'world')")
    first.execute("DELETE FROM notes WHERE id = 1")

    second = Database(tmp_path)
    assert second.execute("SELECT * FROM notes").rows == [(2, "world")]
    assert second.execute("EXPLAIN SELECT * FROM notes WHERE body = 'world'").rows == [
        ("INDEX LOOKUP notes.body = 'world'",)
    ]
    second.execute("INSERT INTO notes VALUES (3, 'again')")
    assert second.execute("SELECT COUNT(*) FROM notes").rows == [(2,)]

    second.execute("DROP TABLE notes")
    assert not (tmp_path / "notes.json").exists()


def test_matches_sqlite_on_random_queries():
    """Differential test: random data and WHERE clauses must agree with SQLite."""
    rng = random.Random(7)
    ours = Database()
    theirs = sqlite3.connect(":memory:")
    schema = "CREATE TABLE t (id INTEGER PRIMARY KEY, a INTEGER, b INTEGER, c TEXT)"
    ours.execute(schema)
    ours.execute("CREATE INDEX idx_a ON t (a)")
    theirs.execute(schema)

    for i in range(1, 301):
        a = rng.choice([None, *range(20)])
        b = rng.randrange(-50, 50)
        c = rng.choice(["x", "y", "z"])
        values = (i, a, b, c)
        ours.execute(f"INSERT INTO t VALUES ({i}, {'NULL' if a is None else a}, {b}, '{c}')")
        theirs.execute("INSERT INTO t VALUES (?, ?, ?, ?)", values)

    def predicate():
        column = rng.choice(["id", "a", "b"])
        op = rng.choice(["=", "!=", "<", "<=", ">", ">="])
        clause = f"{column} {op} {rng.randrange(-10, 40)}"
        roll = rng.random()
        if roll < 0.2:
            return f"NOT ({clause})"
        if roll < 0.3:
            return f"{column} IS NULL"
        if roll < 0.35:
            return f"c = '{rng.choice('xyz')}'"
        return clause

    for _ in range(300):
        where = predicate()
        for _ in range(rng.randrange(3)):
            where = f"({where}) {rng.choice(['AND', 'OR'])} ({predicate()})"
        sql = f"SELECT id FROM t WHERE {where} ORDER BY id"
        expected = theirs.execute(sql).fetchall()
        assert ours.execute(sql).rows == expected, sql
