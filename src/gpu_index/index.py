"""Turn ledger observations into one daily index value per GPU model and tier.

The full rules, in plain language, are in METHODOLOGY.md. Each series is one exact
product (e.g. "H100 SXM 80GB, on-demand"): an H100 NVL and an H100 SXM are different
products at very different prices, and pooling them would make the index track the
product mix instead of the price. For each series and UTC day:

  1. Snapshot   each source contributes its latest snapshot taken on or before
                that day, if it is at most 35 days old. Sources that publish
                daily (Azure) count with that day's prices; list prices that
                change rarely (AWS publishes about monthly) stay valid until
                replaced. A source never counts twice.
  2. Weights    within a source, every region counts equally, and within a
                region every machine size counts equally.
  3. Outliers   drop prices more than 3x above or below the median. That catches
                data errors (a whole-machine price read as per-GPU, a misplaced
                decimal) while keeping genuine regional premiums.
  4. Value      each source's weighted median, then the plain average of those
                medians, so every source counts equally. (A single pooled median
                lands on whichever price sits at the boundary between sources
                and jumps when one listing changes.) The 25th-75th percentile
                range is taken over all prices, weighting sources equally.
  5. Minimum    a source needs at least 3 prices to count, and the day needs at
                least 5 in total; otherwise nothing is published. A missing
                value is better than a misleading one.

Everything here is a pure function of the ledger: same ledger in, byte-identical
files out.
"""

from __future__ import annotations

import bisect
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .ledger import Ledger

METHODOLOGY_VERSION = "1.1"
TIERS = ("on_demand", "spot")
MIN_OBSERVATIONS = 5
MIN_PER_SOURCE = 3
OUTLIER_FACTOR = 3.0
MAX_SNAPSHOT_AGE_DAYS = 35


@dataclass(frozen=True)
class Point:
    date: str
    value: float
    p25: float
    p75: float
    used: int
    dropped: int
    sources: dict[str, int]
    source_dates: dict[str, str] = field(default_factory=dict)  # when each source's prices were taken

    def as_dict(self) -> dict:
        return {
            "date": self.date, "value": self.value, "p25": self.p25, "p75": self.p75,
            "observations_used": self.used, "outliers_dropped": self.dropped, "sources": self.sources,
            "source_dates": self.source_dates,
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


def daily_snapshots(obs: list[dict]) -> dict[str, list[dict]]:
    """For every calendar day from the first observation to the last: each source's
    latest snapshot taken on or before that day, if at most MAX_SNAPSHOT_AGE_DAYS old.

    A snapshot is every observation a source recorded at one moment (`observed_at`).
    """
    by_source: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for o in obs:
        by_source[o["source"]][o["observed_at"]].append(o)
    if not by_source:
        return {}
    times = {src: sorted(snaps) for src, snaps in by_source.items()}
    first = date.fromisoformat(min(t[0] for t in times.values())[:10])
    last = date.fromisoformat(max(t[-1] for t in times.values())[:10])

    days: dict[str, list[dict]] = {}
    d = first
    while d <= last:
        end_of_day = d.isoformat() + "T23:59:59Z"
        rows: list[dict] = []
        for src, ts in times.items():
            i = bisect.bisect_right(ts, end_of_day) - 1
            if i < 0:
                continue
            taken = date.fromisoformat(ts[i][:10])
            if (d - taken).days <= MAX_SNAPSHOT_AGE_DAYS:
                rows.extend(by_source[src][ts[i]])
        days[d.isoformat()] = rows
        d += timedelta(days=1)
    return days


def compute_point(day: str, obs: list[dict]) -> Point | None:
    obs = [o for o in obs if o["usd_per_gpu_hour"] > 0]
    kept, dropped = drop_outliers(obs)
    by_source: dict[str, list[dict]] = defaultdict(list)
    for o in kept:
        by_source[o["source"]].append(o)
    by_source = {src: rows for src, rows in sorted(by_source.items()) if len(rows) >= MIN_PER_SOURCE}
    kept = [o for rows in by_source.values() for o in rows]
    if len(kept) < MIN_OBSERVATIONS:
        return None

    source_medians = []
    for rows in by_source.values():
        w = nested_weights(rows)
        source_medians.append(weighted_quantile([o["usd_per_gpu_hour"] for o in rows], w, 0.5))
    value = sum(source_medians) / len(source_medians)

    prices = [o["usd_per_gpu_hour"] for o in kept]
    w = nested_weights(kept)  # every source gets an equal share of the range too
    taken = {src: max(o["observed_at"] for o in rows)[:10] for src, rows in by_source.items()}
    r = lambda x: round(x, 4)  # noqa: E731
    return Point(
        date=day,
        value=r(value),
        p25=r(weighted_quantile(prices, w, 0.25)),
        p75=r(weighted_quantile(prices, w, 0.75)),
        used=len(kept), dropped=len(dropped),
        sources={src: len(rows) for src, rows in by_source.items()},
        source_dates=taken,
    )


def slug(gpu_model: str, tier: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{gpu_model} {tier}".lower()).strip("-")


def compute_series(observations: list[dict]) -> dict[tuple[str, str, str], list[Point]]:
    """Keyed by (gpu, gpu_model, tier)."""
    days = daily_snapshots(observations)
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
