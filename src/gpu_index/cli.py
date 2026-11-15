"""Command line:  python -m gpu_index {collect,verify,summary}"""

from __future__ import annotations

import argparse
import statistics
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .ledger import Ledger, iso
from .sources import azure

SOURCES = {"azure": azure}


def collect(ledger: Ledger, source_name: str) -> int:
    source = SOURCES[source_name]
    started = datetime.now(timezone.utc).replace(microsecond=0)
    observed_at = iso(started)
    run = {"kind": "run", "run_id": f"{source_name}-{observed_at}", "source": source_name, "started_at": observed_at}
    try:
        items = source.fetch_items()
        observations, skipped = source.parse(items, observed_at)
    except Exception as exc:  # the failure itself is recorded, so the gap is explained
        run.update(status="error", error=f"{type(exc).__name__}: {exc}", observations=0)
        ledger.append([run])
        traceback.print_exc()
        return 1
    run.update(status="ok", raw_items=len(items), observations=len(observations), skipped=skipped)
    written = ledger.append([*observations, run])
    print(f"{source_name}: {len(items)} price rows -> {len(observations)} observations "
          f"({len(written) - 1} new). Skipped: {skipped}")
    return 0


def verify(ledger: Ledger) -> int:
    r = ledger.verify()
    if r.ok:
        print(f"OK: {r.entries} entries in {r.files} files, hash chain intact.")
        return 0
    print(f"FAILED: {len(r.problems)} problem(s) in {r.entries} entries:")
    for p in r.problems:
        print("  -", p)
    return 1


def summary(ledger: Ledger) -> int:
    obs = ledger.observations()
    if not obs:
        print("Ledger is empty. Run: python -m gpu_index collect azure")
        return 0
    latest = max(o["observed_at"] for o in obs)
    rows = [o for o in obs if o["observed_at"] == latest]
    print(f"Latest snapshot {latest}  (USD per GPU-hour, across regions and machine sizes)\n")
    print(f"{'GPU':<8}{'tier':<11}{'n':>4}{'min':>9}{'median':>9}{'max':>9}")
    for gpu in sorted({o["gpu"] for o in rows}):
        for tier in ("on_demand", "spot"):
            p = sorted(o["usd_per_gpu_hour"] for o in rows if o["gpu"] == gpu and o["tier"] == tier)
            if p:
                print(f"{gpu:<8}{tier:<11}{len(p):>4}{p[0]:>9.2f}{statistics.median(p):>9.2f}{p[-1]:>9.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gpu_index", description=__doc__)
    parser.add_argument("--ledger", type=Path, default=Path("ledger"), help="ledger folder (default: ./ledger)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="fetch prices from a source and append them to the ledger")
    c.add_argument("source", choices=sorted(SOURCES))
    sub.add_parser("verify", help="check the hash chain and file layout of the whole ledger")
    sub.add_parser("summary", help="print min/median/max per GPU from the latest snapshot")
    args = parser.parse_args(argv)
    ledger = Ledger(args.ledger)
    if args.cmd == "collect":
        return collect(ledger, args.source)
    if args.cmd == "verify":
        return verify(ledger)
    return summary(ledger)


if __name__ == "__main__":
    sys.exit(main())
