"""AWS list prices from the public EC2 Price List bulk files (no account or key needed).

Docs: https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/using-ppslong.html

AWS publishes a new version of its EC2 price list roughly monthly, as one CSV file
per region (about 6 GB in total). So each run:

  1. Reads the tiny version index (about 30 KB).
  2. If the ledger already has this version, records an "unchanged" run and stops.
  3. Otherwise streams every region's CSV line by line, keeping only the rows for
     machines in the catalog. Nothing big is ever written to disk.

We keep Linux, shared-tenancy, on-demand prices. Capacity Blocks (pre-booked slots,
listed at $0 because they are priced by auction), Windows/RHEL/SUSE (OS licence
included), dedicated hosts and reserved/savings-plan terms are skipped. The price
list has no spot prices.
"""

from __future__ import annotations

import csv
from typing import Callable, Iterable, Iterator

from ..catalog import AWS
from ..ledger import Ledger, observation_id
from . import Fetch
from .http import get_json, stream_lines

SOURCE = "aws"
BASE = "https://pricing.us-east-1.amazonaws.com"
OFFER = "/offers/v1.0/aws/AmazonEC2"

# Exact quoted cells, so "p5.48xlarge" never matches "p5en.48xlarge".
_NEEDLES = tuple(f'"{t}"'.encode() for t in AWS)


def current_version(get: Callable[[str], dict] = get_json) -> str:
    return get(BASE + OFFER + "/index.json")["currentVersion"]


def region_csv_urls(version: str, get: Callable[[str], dict] = get_json) -> list[str]:
    regions = get(f"{BASE}{OFFER}/{version}/region_index.json")["regions"]
    return [BASE + r["currentVersionUrl"].replace("/index.json", "/index.csv") for _, r in sorted(regions.items())]


def gpu_rows(lines: Iterable[bytes]) -> Iterator[dict]:
    """Yield catalog rows from one region CSV, plus the file's version metadata on each row.

    The file starts with a few "key","value" metadata lines, then a header row
    beginning with "SKU", then hundreds of thousands of price rows.
    """
    meta: dict[str, str] = {}
    header: list[str] | None = None
    for raw in lines:
        if header is None:
            cells = next(csv.reader([raw.decode("utf-8")]))
            if cells and cells[0] == "SKU":
                header = cells
            elif len(cells) == 2:
                meta[cells[0]] = cells[1]
            continue
        if not any(n in raw for n in _NEEDLES):
            continue  # the cheap check that makes scanning 6 GB fast
        row = dict(zip(header, next(csv.reader([raw.decode("utf-8")]))))
        row["_version"] = meta.get("Version", "")
        row["_published"] = meta.get("Publication Date", "")
        yield row


def fetch_items(version: str, get: Callable[[str], dict] = get_json,
                stream: Callable[[str], Iterable[bytes]] = stream_lines) -> list[dict]:
    items: list[dict] = []
    for url in region_csv_urls(version, get):
        items.extend(gpu_rows(stream(url)))
    return items


def last_version(ledger: Ledger) -> str | None:
    """The newest AWS version a successful run already collected."""
    version = None
    for e in ledger.entries():
        if e.get("kind") == "run" and e.get("source") == SOURCE and e.get("status") == "ok" and e.get("version"):
            version = e["version"]
    return version


def fetch(ledger: Ledger) -> Fetch:
    version = current_version()
    if version == last_version(ledger):
        return Fetch(unchanged=True, meta={"version": version})
    return Fetch(items=fetch_items(version), meta={"version": version})


def parse(items: list[dict], observed_at: str) -> tuple[list[dict], dict[str, int]]:
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    best: dict[tuple[str, str], dict] = {}
    for r in items:
        if r.get("Instance Type") not in AWS:
            skip("not in catalog"); continue
        if r.get("TermType") != "OnDemand":
            skip("not on-demand"); continue
        if r.get("MarketOption") != "OnDemand":
            skip("capacity block"); continue
        # plain Linux is exactly operation "RunInstances"; RHEL, SUSE, Windows etc. have suffixed codes
        if (r.get("Operating System") != "Linux" or r.get("Pre Installed S/W") != "NA"
                or r.get("operation") != "RunInstances"):
            skip("os or software licence"); continue
        if r.get("Tenancy") != "Shared" or r.get("CapacityStatus") != "Used":
            skip("dedicated or reservation"); continue
        if r.get("Currency") != "USD" or r.get("Unit") != "Hrs":
            skip("unexpected unit"); continue
        try:
            price = float(r.get("PricePerUnit", "0"))
        except ValueError:
            skip("unreadable price"); continue
        if price <= 0:
            skip("zero price"); continue
        key = (r["Instance Type"], r["Region Code"])
        if key in best:
            skip("duplicate row"); continue
        best[key] = {**r, "_price": price}

    observations = []
    for (instance, region), r in sorted(best.items()):
        machine = AWS[instance]
        price = r["_price"]
        observations.append({
            "kind": "observation",
            "id": observation_id(SOURCE, instance, region, "on_demand", observed_at),
            "observed_at": observed_at,
            "source": SOURCE,
            "gpu": machine.gpu,
            "gpu_model": machine.gpu_model,
            "gpu_count": machine.gpu_count,
            "instance": instance,
            "region": region,
            "tier": "on_demand",
            "usd_per_hour": round(price, 6),
            "usd_per_gpu_hour": round(price / machine.gpu_count, 6),
            "source_ref": {
                "sku": r.get("SKU"),
                "rate_code": r.get("RateCode"),
                "effective_from": r.get("EffectiveDate"),
                "price_list_version": r.get("_version"),
                "published": r.get("_published"),
            },
        })
    return observations, skipped
