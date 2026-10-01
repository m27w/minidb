"""Recursive-descent parser: turns tokens into AST statements.

Expression grammar (lowest to highest precedence):

    expr       := or_expr
    or_expr    := and_expr ("OR" and_expr)*
    and_expr   := not_expr ("AND" not_expr)*
    not_expr   := "NOT" not_expr | comparison
    comparison := additive (("=" | "!=" | "<" | "<=" | ">" | ">=") additive
                           | "IS" ["NOT"] "NULL")?
    additive   := term (("+" | "-") term)*
    term       := unary (("*" | "/") unary)*
    unary      := "-" unary | primary
    primary    := NUMBER | STRING | TRUE | FALSE | NULL | IDENT | "(" expr ")"
"""

from __future__ import annotations

from . import ast
from .tokenizer import SQLSyntaxError, Token, TokenType, tokenize

COLUMN_TYPES = ("INTEGER", "REAL", "TEXT")
COMPARISON_OPS = ("=", "!=", "<", "<=", ">", ">=")


def parse(sql: str) -> list[ast.Statement]:
    """Parse one or more semicolon-separated statements."""
    return Parser(tokenize(sql)).parse_script()


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    # ------------------------------------------------------------- helpers

    @property
    def current(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        token = self.tokens[self.pos]
        if token.type is not TokenType.EOF:
            self.pos += 1
        return token

    def error(self, message: str) -> SQLSyntaxError:
        return SQLSyntaxError(message, self.current.position)

    def expect_keyword(self, word: str) -> Token:
        if not self.current.is_keyword(word):
            raise self.error(f"Expected {word}")
        return self.advance()

    def expect_symbol(self, symbol: str) -> Token:
        if not self.current.is_symbol(symbol):
            raise self.error(f"Expected '{symbol}'")
        return self.advance()

    def expect_ident(self) -> str:
        if self.current.type is not TokenType.IDENT:
            raise self.error("Expected an identifier")
        return self.advance().value  # type: ignore[return-value]

    def accept_keyword(self, word: str) -> bool:
        if self.current.is_keyword(word):
            self.advance()
            return True
        return False

    def accept_symbol(self, symbol: str) -> bool:
        if self.current.is_symbol(symbol):
            self.advance()
            return True
        return False

    def comma_separated(self, parse_item):
        items = [parse_item()]
        while self.accept_symbol(","):
            items.append(parse_item())
        return items

    # ---------------------------------------------------------- statements

    def parse_script(self) -> list[ast.Statement]:
        statements: list[ast.Statement] = []
        while self.current.type is not TokenType.EOF:
            if self.accept_symbol(";"):
                continue
            statements.append(self.parse_statement())
            if self.current.type is not TokenType.EOF:
                self.expect_symbol(";")
        return statements

    def parse_statement(self) -> ast.Statement:
        token = self.current
        if token.is_keyword("SELECT"):
            return self.parse_select()
        if token.is_keyword("INSERT"):
            return self.parse_insert()
        if token.is_keyword("UPDATE"):
            return self.parse_update()
        if token.is_keyword("DELETE"):
            return self.parse_delete()
        if token.is_keyword("CREATE"):
            return self.parse_create()
        if token.is_keyword("DROP"):
            self.advance()
            self.expect_keyword("TABLE")
            return ast.DropTable(self.expect_ident())
        if token.is_keyword("EXPLAIN"):
            self.advance()
            if not self.current.is_keyword("SELECT"):
                raise self.error("EXPLAIN only supports SELECT")
            return ast.Explain(self.parse_select())
        raise self.error("Expected a statement")

    def parse_create(self) -> ast.Statement:
        self.expect_keyword("CREATE")
        if self.accept_keyword("INDEX"):
            name = self.expect_ident()
            self.expect_keyword("ON")
            table = self.expect_ident()
            self.expect_symbol("(")
            column = self.expect_ident()
            self.expect_symbol(")")
            return ast.CreateIndex(name, table, column)

        self.expect_keyword("TABLE")
        table = self.expect_ident()
        self.expect_symbol("(")
        columns = self.comma_separated(self.parse_column_def)
        self.expect_symbol(")")

        if sum(c.primary_key for c in columns) > 1:
            raise self.error("Only one PRIMARY KEY column is supported")
        names = [c.name for c in columns]
        if len(names) != len(set(names)):
            raise self.error("Duplicate column name")
        return ast.CreateTable(table, tuple(columns))

    def parse_column_def(self) -> ast.ColumnDef:
        name = self.expect_ident()
        type_token = self.current
        if not type_token.is_keyword(*COLUMN_TYPES):
            raise self.error("Expected a column type (INTEGER, REAL or TEXT)")
        self.advance()
        primary_key = False
        if self.accept_keyword("PRIMARY"):
            self.expect_keyword("KEY")
            primary_key = True
        return ast.ColumnDef(name, type_token.value, primary_key)  # type: ignore[arg-type]

    def parse_insert(self) -> ast.Insert:
        self.expect_keyword("INSERT")
        self.expect_keyword("INTO")
        table = self.expect_ident()

        columns = None
        if self.accept_symbol("("):
            columns = tuple(self.comma_separated(self.expect_ident))
            self.expect_symbol(")")

        self.expect_keyword("VALUES")

        def row():
            self.expect_symbol("(")
            values = tuple(self.comma_separated(self.parse_expr))
            self.expect_symbol(")")
            return values

        return ast.Insert(table, columns, tuple(self.comma_separated(row)))

    def parse_select(self) -> ast.Select:
        self.expect_keyword("SELECT")
        count_star = False
        columns: tuple[str, ...] | None = None

        if self.accept_symbol("*"):
            columns = None
        elif self.accept_keyword("COUNT"):
            self.expect_symbol("(")
            self.expect_symbol("*")
            self.expect_symbol(")")
            count_star = True
        else:
            columns = tuple(self.comma_separated(self.expect_ident))

        self.expect_keyword("FROM")
        table = self.expect_ident()
        where = self.parse_where()

        order_by = None
        if self.accept_keyword("ORDER"):
            self.expect_keyword("BY")
            column = self.expect_ident()
            descending = False
            if self.accept_keyword("DESC"):
                descending = True
            else:
                self.accept_keyword("ASC")
            order_by = ast.OrderBy(column, descending)

        limit = None
        if self.accept_keyword("LIMIT"):
            token = self.current
            if token.type is not TokenType.NUMBER or not isinstance(token.value, int) or token.value < 0:
                raise self.error("LIMIT must be a non-negative integer")
            limit = self.advance().value

        return ast.Select(table, columns, count_star, where, order_by, limit)  # type: ignore[arg-type]

    def parse_update(self) -> ast.Update:
        self.expect_keyword("UPDATE")
        table = self.expect_ident()
        self.expect_keyword("SET")

        def assignment():
            column = self.expect_ident()
            self.expect_symbol("=")
            return column, self.parse_expr()

        assignments = tuple(self.comma_separated(assignment))
        return ast.Update(table, assignments, self.parse_where())

    def parse_delete(self) -> ast.Delete:
        self.expect_keyword("DELETE")
        self.expect_keyword("FROM")
        table = self.expect_ident()
        return ast.Delete(table, self.parse_where())

    def parse_where(self) -> ast.Expr | None:
        if self.accept_keyword("WHERE"):
            return self.parse_expr()
        return None

    # --------------------------------------------------------- expressions

    def parse_expr(self) -> ast.Expr:
        return self.parse_or()

    def parse_or(self) -> ast.Expr:
        left = self.parse_and()
        while self.accept_keyword("OR"):
            left = ast.BinaryOp("OR", left, self.parse_and())
        return left

    def parse_and(self) -> ast.Expr:
        left = self.parse_not()
        while self.accept_keyword("AND"):
            left = ast.BinaryOp("AND", left, self.parse_not())
        return left

    def parse_not(self) -> ast.Expr:
        if self.accept_keyword("NOT"):
            return ast.UnaryOp("NOT", self.parse_not())
        return self.parse_comparison()

    def parse_comparison(self) -> ast.Expr:
        left = self.parse_additive()
        if self.current.is_symbol(*COMPARISON_OPS):
            op = self.advance().value
            return ast.BinaryOp(op, left, self.parse_additive())  # type: ignore[arg-type]
        if self.accept_keyword("IS"):
            negated = self.accept_keyword("NOT")
            self.expect_keyword("NULL")
            return ast.IsNull(left, negated)
        return left

    def parse_additive(self) -> ast.Expr:
        left = self.parse_term()
        while self.current.is_symbol("+", "-"):
            op = self.advance().value
            left = ast.BinaryOp(op, left, self.parse_term())  # type: ignore[arg-type]
        return left

    def parse_term(self) -> ast.Expr:
        left = self.parse_unary()
        while self.current.is_symbol("*", "/"):
            op = self.advance().value
            left = ast.BinaryOp(op, left, self.parse_unary())  # type: ignore[arg-type]
        return left

    def parse_unary(self) -> ast.Expr:
        if self.accept_symbol("-"):
            return ast.UnaryOp("-", self.parse_unary())
        return self.parse_primary()

    def parse_primary(self) -> ast.Expr:
        token = self.current
        if token.type in (TokenType.NUMBER, TokenType.STRING):
            self.advance()
            return ast.Literal(token.value)
        if token.is_keyword("NULL"):
            self.advance()
            return ast.Literal(None)
        if token.is_keyword("TRUE", "FALSE"):
            self.advance()
            return ast.Literal(token.value == "TRUE")
        if token.type is TokenType.IDENT:
            self.advance()
            return ast.Column(token.value)  # type: ignore[arg-type]
        if self.accept_symbol("("):
            expr = self.parse_expr()
            self.expect_symbol(")")
            return expr
        raise self.error("Expected an expression")
