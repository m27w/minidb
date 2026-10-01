"""Abstract syntax tree node types produced by the parser."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


# ---------------------------------------------------------------- expressions

@dataclass(frozen=True)
class Literal:
    value: object  # int, float, str, bool or None


@dataclass(frozen=True)
class Column:
    name: str


@dataclass(frozen=True)
class UnaryOp:
    op: str  # "NOT" or "-"
    operand: "Expr"


@dataclass(frozen=True)
class BinaryOp:
    op: str  # = != < <= > >= AND OR + - * /
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class IsNull:
    operand: "Expr"
    negated: bool = False


Expr = Union[Literal, Column, UnaryOp, BinaryOp, IsNull]


# ----------------------------------------------------------------- statements

@dataclass(frozen=True)
class ColumnDef:
    name: str
    type: str  # INTEGER, REAL or TEXT
    primary_key: bool = False


@dataclass(frozen=True)
class CreateTable:
    table: str
    columns: tuple[ColumnDef, ...]


@dataclass(frozen=True)
class CreateIndex:
    name: str
    table: str
    column: str


@dataclass(frozen=True)
class DropTable:
    table: str


@dataclass(frozen=True)
class Insert:
    table: str
    columns: tuple[str, ...] | None
    rows: tuple[tuple[Expr, ...], ...]


@dataclass(frozen=True)
class OrderBy:
    column: str
    descending: bool = False


@dataclass(frozen=True)
class Select:
    table: str
    columns: tuple[str, ...] | None  # None means SELECT *
    count_star: bool = False
    where: Expr | None = None
    order_by: OrderBy | None = None
    limit: int | None = None


@dataclass(frozen=True)
class Update:
    table: str
    assignments: tuple[tuple[str, Expr], ...]
    where: Expr | None = None


@dataclass(frozen=True)
class Delete:
    table: str
    where: Expr | None = None


@dataclass(frozen=True)
class Explain:
    statement: Select


Statement = Union[CreateTable, CreateIndex, DropTable, Insert, Select, Update, Delete, Explain]
