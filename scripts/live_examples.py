"""Run every example SQL of the given sources against live APIs and classify the outcome.

    GRAFANA_URL=... PROMETHEUS_URL=... uv run --no-sync python scripts/live_examples.py grafana prometheus

Outcomes: ok (rows), empty (0 rows; fine for examples with illustrative filter values), error (the
request shape, auth, parsing or SQL failed - a manifest bug unless the example needs data that does
not exist). Exit status is 1 when any example errors.
"""

from __future__ import annotations

import asyncio
import sys

from omniops.engine.catalog import Catalog
from omniops.engine.engine import SQLEngine
from omniops.engine.timeutil import TimeAnchor


async def main(sources: list[str]) -> int:
    cat = Catalog.from_env(only=sources)
    missing = [s for s in sources if s not in cat.runtimes]
    if missing:
        print(f"not configured: {missing} (unavailable: {cat.unavailable})")
    anchor = TimeAnchor.wall_clock()
    errors = 0
    counts = {"ok": 0, "empty": 0, "error": 0}
    for name, entry in sorted(cat.entries.items()):
        if entry.computed or entry.runtime is None or entry.runtime.name not in sources:
            continue
        if entry.table.kind == "write":
            continue
        for ex in entry.table.examples:
            engine = SQLEngine(cat, anchor, positive_controls=False)
            try:
                res = await asyncio.wait_for(engine.query(ex.sql), timeout=60)
                status = "ok" if res.total_rows else "empty"
                detail = f"{res.total_rows} rows" + (f"; {res.notes[0][:120]}" if res.notes else "")
            except Exception as exc:  # noqa: BLE001 - report every failure
                status, detail = "error", f"{type(exc).__name__}: {str(exc)[:300]}"
                errors += 1
            finally:
                engine.close()
            counts[status] += 1
            print(f"{status.upper():5} {name}: {detail}")
            if status == "error":
                print(f"      sql: {' '.join(ex.sql.split())[:300]}")
    await cat.aclose()
    print(f"\n{counts}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
