"""Lexical analysis: turns a SQL string into a flat list of tokens."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class TokenType(Enum):
    KEYWORD = auto()
    IDENT = auto()
    NUMBER = auto()
    STRING = auto()
    SYMBOL = auto()
    EOF = auto()


KEYWORDS = {
    "SELECT", "FROM", "WHERE", "INSERT", "INTO", "VALUES", "UPDATE", "SET",
    "DELETE", "CREATE", "TABLE", "INDEX", "ON", "DROP", "AND", "OR", "NOT",
    "NULL", "IS", "ORDER", "BY", "ASC", "DESC", "LIMIT", "PRIMARY", "KEY",
    "INTEGER", "REAL", "TEXT", "COUNT", "EXPLAIN", "TRUE", "FALSE",
}

# Longest symbols first so "<=" is matched before "<".
SYMBOLS = ["<=", ">=", "!=", "<>", "(", ")", ",", "*", "=", "<", ">", ";", "+", "-", "/"]


class SQLSyntaxError(Exception):
    """Raised for any malformed SQL, with the character position where it failed."""

    def __init__(self, message: str, position: int | None = None):
        self.position = position
        where = f" at position {position}" if position is not None else ""
        super().__init__(f"{message}{where}")


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: object
    position: int

    def is_keyword(self, *words: str) -> bool:
        return self.type is TokenType.KEYWORD and self.value in words

    def is_symbol(self, *symbols: str) -> bool:
        return self.type is TokenType.SYMBOL and self.value in symbols


def tokenize(sql: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(sql)

    while i < n:
        ch = sql[i]

        if ch.isspace():
            i += 1
            continue

        # Line comments: -- comment
        if sql.startswith("--", i):
            newline = sql.find("\n", i)
            i = n if newline == -1 else newline + 1
            continue

        if ch.isalpha() or ch == "_":
            start = i
            while i < n and (sql[i].isalnum() or sql[i] == "_"):
                i += 1
            word = sql[start:i]
            upper = word.upper()
            if upper in KEYWORDS:
                tokens.append(Token(TokenType.KEYWORD, upper, start))
            else:
                tokens.append(Token(TokenType.IDENT, word.lower(), start))
            continue

        if ch.isdigit() or (ch == "." and i + 1 < n and sql[i + 1].isdigit()):
            start = i
            seen_dot = False
            while i < n and (sql[i].isdigit() or (sql[i] == "." and not seen_dot)):
                if sql[i] == ".":
                    seen_dot = True
                i += 1
            text = sql[start:i]
            value = float(text) if seen_dot else int(text)
            tokens.append(Token(TokenType.NUMBER, value, start))
            continue

        if ch == "'":
            start = i
            i += 1
            chars: list[str] = []
            while True:
                if i >= n:
                    raise SQLSyntaxError("Unterminated string literal", start)
                if sql[i] == "'":
                    # Two single quotes inside a string is an escaped quote.
                    if i + 1 < n and sql[i + 1] == "'":
                        chars.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                chars.append(sql[i])
                i += 1
            tokens.append(Token(TokenType.STRING, "".join(chars), start))
            continue

        for symbol in SYMBOLS:
            if sql.startswith(symbol, i):
                normalised = "!=" if symbol == "<>" else symbol
                tokens.append(Token(TokenType.SYMBOL, normalised, i))
                i += len(symbol)
                break
        else:
            raise SQLSyntaxError(f"Unexpected character {ch!r}", i)

    tokens.append(Token(TokenType.EOF, None, n))
    return tokens
