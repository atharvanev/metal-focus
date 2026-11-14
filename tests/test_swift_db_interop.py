"""Interop oracle for the Swift app: can Python's data.db read a DB the app created?

The Swift test (DatabaseTests.pythonReadsSwiftDB) runs:
    uv run python tests/test_swift_db_interop.py <db path> <hex key>
and expects stdout "samples=<n> title=<first title>".
"""

import sys
from pathlib import Path


def main(db: str, key: str) -> None:
    from data.db import connect

    conn = connect(Path(db), key)
    n = conn.execute("SELECT count(*) FROM samples").fetchone()[0]
    title = conn.execute("SELECT window_title FROM samples ORDER BY id LIMIT 1").fetchone()
    print(f"samples={n} title={title[0] if title else None}")


def test_module_imports():
    assert callable(main)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main(sys.argv[1], sys.argv[2])
