"""Interactive shell: python -m minidb [data_directory]"""

from __future__ import annotations

import sys

from .engine import Database, ExecutionError, Result
from .tokenizer import SQLSyntaxError

HELP = """\
Statements end with ';'. Meta-commands:
  .tables          list tables
  .schema [table]  show column definitions and indexes
  .help            show this message
  .quit            exit"""


def format_table(result: Result) -> str:
    if not result.columns:
        return result.message
    header = [str(c) for c in result.columns]
    body = [["NULL" if v is None else str(v) for v in row] for row in result.rows]
    widths = [max(len(h), *(len(r[i]) for r in body)) if body else len(h) for i, h in enumerate(header)]
    line = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    fmt = lambda cells: "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths)) + " |"
    out = [line, fmt(header), line, *(fmt(r) for r in body), line]
    out.append(f"{len(result.rows)} row(s)")
    return "\n".join(out)


def meta_command(db: Database, command: str) -> bool:
    parts = command.split()
    name = parts[0]
    if name in (".quit", ".exit"):
        return False
    if name == ".help":
        print(HELP)
    elif name == ".tables":
        print("\n".join(sorted(db.tables)) or "(no tables)")
    elif name == ".schema":
        tables = [db.tables[parts[1]]] if len(parts) > 1 and parts[1] in db.tables else db.tables.values()
        for table in tables:
            cols = ", ".join(f"{c.name} {c.type}{' PRIMARY KEY' if c.primary_key else ''}" for c in table.columns)
            print(f"CREATE TABLE {table.name} ({cols});")
            for index_name, column in table.indexes.items():
                print(f"  index {index_name} on ({column})")
    else:
        print(f"Unknown command {name}. Try .help")
    return True


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    db = Database(argv[1] if len(argv) > 1 else None)
    where = f"'{argv[1]}'" if len(argv) > 1 else "memory"
    print(f"minidb shell ({where}). Type .help for help.")
    buffer = ""
    while True:
        try:
            line = input("minidb> " if not buffer else "   ...> ")
        except EOFError:
            print()
            return 0
        if not buffer and line.strip().startswith("."):
            if not meta_command(db, line.strip()):
                return 0
            continue
        buffer += line + "\n"
        if not buffer.rstrip().endswith(";"):
            continue
        try:
            print(format_table(db.execute(buffer)))
        except (SQLSyntaxError, ExecutionError) as exc:
            print(f"Error: {exc}")
        buffer = ""


if __name__ == "__main__":
    sys.exit(main(sys.argv))
