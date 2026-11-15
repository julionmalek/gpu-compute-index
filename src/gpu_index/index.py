"""Turn ledger observations into one daily index value per GPU model and tier.

The full rules, in plain language, are in METHODOLOGY.md. Each series is one exact
product (e.g. "H100 SXM 80GB, on-demand"): an H100 NVL and an H100 SXM are different
products at very different prices, and pooling them would make the index track the
product mix instead of the price. For each series and UTC day:

  1. Snapshot   take each source's latest snapshot that day (no double counting).
  2. Weights    every source counts equally; within a source every region counts
                equally; within a region every machine size counts equally.
  3. Outliers   drop prices more than 3x above or below the median. That catches
                data errors (a whole-machine price read as per-GPU, a misplaced
                decimal) while keeping genuine regional premiums.
  4. Value      the weighted median of what is left, with the weighted 25th and
                75th percentiles as a range.
  5. Minimum    publish nothing if fewer than 5 prices survive. A missing value is
                better than a misleading one.

Everything here is a pure function of the ledger: same ledger in, byte-identical
files out.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .ledger import Ledger

METHODOLOGY_VERSION = "1.0"
TIERS = ("on_demand", "spot")
MIN_OBSERVATIONS = 5
OUTLIER_FACTOR = 3.0


@dataclass(frozen=True)
class Point:
    date: str
    value: float
    p25: float
    p75: float
    used: int
    dropped: int
    sources: dict[str, int]

    def as_dict(self) -> dict:
        return {
            "date": self.date, "value": self.value, "p25": self.p25, "p75": self.p75,
            "observations_used": self.used, "outliers_dropped": self.dropped, "sources": self.sources,
        }


def weighted_quantile(values: list[float], weights: list[float], q: float) -> float:
    """Smallest value whose cumulative weight reaches q of the total."""
    if not values:
        raise ValueError("no values")
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    target = q * total
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= target - 1e-12:
            return v
    return pairs[-1][0]


def nested_weights(obs: list[dict]) -> list[float]:
    """Source, then region within source, then machine within region: equal shares at each level."""
    per_source: dict[str, set] = defaultdict(set)
    per_region: dict[tuple, int] = defaultdict(int)
    for o in obs:
        per_source[o["source"]].add(o["region"])
        per_region[(o["source"], o["region"])] += 1
    n_sources = len(per_source)
    return [
        1.0 / n_sources / len(per_source[o["source"]]) / per_region[(o["source"], o["region"])]
        for o in obs
    ]


def median(values: list[float]) -> float:
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def drop_outliers(obs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Drop prices more than OUTLIER_FACTOR times above or below the plain median."""
    if len(obs) < 3:
        return obs, []
    m = median([o["usd_per_gpu_hour"] for o in obs])
    kept, dropped = [], []
    for o in obs:
        ok = m / OUTLIER_FACTOR <= o["usd_per_gpu_hour"] <= m * OUTLIER_FACTOR
        (kept if ok else dropped).append(o)
    return kept, dropped


def latest_snapshots(obs: list[dict]) -> dict[str, list[dict]]:
    """Group observations by UTC day, keeping only each source's last snapshot of that day."""
    by_day_source: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for o in obs:
        by_day_source[(o["observed_at"][:10], o["source"])].append(o)
    days: dict[str, list[dict]] = defaultdict(list)
    for (day, _source), rows in by_day_source.items():
        last = max(r["observed_at"] for r in rows)
        days[day].extend(r for r in rows if r["observed_at"] == last)
    return days


def compute_point(day: str, obs: list[dict]) -> Point | None:
    obs = [o for o in obs if o["usd_per_gpu_hour"] > 0]
    kept, dropped = drop_outliers(obs)
    if len(kept) < MIN_OBSERVATIONS:
        return None
    prices = [o["usd_per_gpu_hour"] for o in kept]
    w = nested_weights(kept)
    sources: dict[str, int] = defaultdict(int)
    for o in kept:
        sources[o["source"]] += 1
    r = lambda x: round(x, 4)  # noqa: E731
    return Point(
        date=day,
        value=r(weighted_quantile(prices, w, 0.5)),
        p25=r(weighted_quantile(prices, w, 0.25)),
        p75=r(weighted_quantile(prices, w, 0.75)),
        used=len(kept), dropped=len(dropped), sources=dict(sorted(sources.items())),
    )


def slug(gpu_model: str, tier: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{gpu_model} {tier}".lower()).strip("-")


def compute_series(observations: list[dict]) -> dict[tuple[str, str, str], list[Point]]:
    """Keyed by (gpu, gpu_model, tier)."""
    days = latest_snapshots(observations)
    keys = sorted({(o["gpu"], o["gpu_model"], o["tier"]) for o in observations})
    series: dict[tuple[str, str, str], list[Point]] = {}
    for gpu, model, tier in keys:
        points = []
        for day in sorted(days):
            rows = [o for o in days[day] if o["gpu_model"] == model and o["tier"] == tier]
            point = compute_point(day, rows) if rows else None
            if point:
                points.append(point)
        if points:
            series[(gpu, model, tier)] = points
    return series


def dump(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build(ledger: Ledger, out: Path | str) -> dict:
    """Recompute every series from the ledger and write them under `out`. Returns latest.json."""
    out = Path(out)
    head, _ = ledger.head()
    series = compute_series(ledger.observations())
    names = {slug(model, tier) for _, model, tier in series}
    (out / "series").mkdir(parents=True, exist_ok=True)
    for old in (out / "series").glob("*.json"):
        if old.stem not in names:
            old.unlink()

    latest = {
        "methodology_version": METHODOLOGY_VERSION,
        "unit": "USD per GPU-hour",
        "ledger_head": head,
        "series": {},
    }
    for (gpu, model, tier), points in sorted(series.items()):
        name = slug(model, tier)
        meta = {"gpu": gpu, "gpu_model": model, "tier": tier}
        (out / "series" / f"{name}.json").write_text(dump({
            **meta, "unit": "USD per GPU-hour", "methodology_version": METHODOLOGY_VERSION,
            "points": [p.as_dict() for p in points],
        }))
        latest["series"][name] = {**meta, **points[-1].as_dict()}
    (out / "latest.json").write_text(dump(latest))
    return latest
