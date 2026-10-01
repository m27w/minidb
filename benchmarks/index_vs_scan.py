"""Compare an indexed lookup against a full table scan as the table grows.

Run from the repo root with: python -m benchmarks.index_vs_scan
"""

import random
import time

from minidb import Database


def timed(db: Database, sql: str, repeats: int = 200) -> float:
    start = time.perf_counter()
    for _ in range(repeats):
        db.execute(sql)
    return (time.perf_counter() - start) / repeats * 1000


def main() -> None:
    rng = random.Random(0)
    print(f"{'rows':>8} | {'index lookup (ms)':>18} | {'full scan (ms)':>15} | speed-up")
    print("-" * 62)
    for size in (1_000, 10_000, 50_000):
        db = Database()
        db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)")
        table = db.tables["t"]
        for i in range(size):  # bulk load through the table API to keep setup fast
            table.insert((i, rng.randrange(1_000_000)))
        target = size // 2
        indexed = timed(db, f"SELECT v FROM t WHERE id = {target}")
        scanned = timed(db, f"SELECT v FROM t WHERE id + 0 = {target}", repeats=5)
        print(f"{size:>8} | {indexed:>18.3f} | {scanned:>15.3f} | {scanned / indexed:>7.0f}x")


if __name__ == "__main__":
    main()
