import pytest

from minidb import ast
from minidb.parser import parse
from minidb.tokenizer import SQLSyntaxError


def one(sql):
    statements = parse(sql)
    assert len(statements) == 1
    return statements[0]


def test_operator_precedence():
    stmt = one("SELECT * FROM t WHERE a = 1 OR b = 2 AND NOT c > 3")
    # AND binds tighter than OR, NOT tighter than AND.
    assert stmt.where == ast.BinaryOp(
        "OR",
        ast.BinaryOp("=", ast.Column("a"), ast.Literal(1)),
        ast.BinaryOp(
            "AND",
            ast.BinaryOp("=", ast.Column("b"), ast.Literal(2)),
            ast.UnaryOp("NOT", ast.BinaryOp(">", ast.Column("c"), ast.Literal(3))),
        ),
    )


def test_arithmetic_precedence_and_parentheses():
    stmt = one("SELECT * FROM t WHERE (a + 2) * 3 = -b")
    assert stmt.where == ast.BinaryOp(
        "=",
        ast.BinaryOp("*", ast.BinaryOp("+", ast.Column("a"), ast.Literal(2)), ast.Literal(3)),
        ast.UnaryOp("-", ast.Column("b")),
    )


def test_full_select():
    stmt = one("SELECT id, name FROM users WHERE age IS NOT NULL ORDER BY name DESC LIMIT 5")
    assert stmt == ast.Select(
        table="users",
        columns=("id", "name"),
        where=ast.IsNull(ast.Column("age"), negated=True),
        order_by=ast.OrderBy("name", descending=True),
        limit=5,
    )


def test_create_table_and_multi_row_insert():
    create, insert = parse(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT); INSERT INTO t (id, name) VALUES (1, 'a'), (2, 'b');"
    )
    assert create.columns[0] == ast.ColumnDef("id", "INTEGER", primary_key=True)
    assert insert.columns == ("id", "name")
    assert len(insert.rows) == 2


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT FROM t",
        "SELECT * FROM",
        "CREATE TABLE t (a BLOB)",
        "CREATE TABLE t (a INTEGER PRIMARY KEY, b INTEGER PRIMARY KEY)",
        "CREATE TABLE t (a INTEGER, a TEXT)",
        "SELECT * FROM t LIMIT -1",
        "SELECT * FROM t WHERE",
        "SELECT * FROM t SELECT * FROM t",
        "EXPLAIN DELETE FROM t",
    ],
)
def test_syntax_errors(sql):
    with pytest.raises(SQLSyntaxError):
        parse(sql)
