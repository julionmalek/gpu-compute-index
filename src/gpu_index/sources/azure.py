"""Azure list prices from the public Retail Prices API (no account or key needed).

Docs: https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices

We keep Linux pay-as-you-go ("on_demand") and Spot prices. Windows meters include an
OS licence, and "Low Priority" meters are a Batch-only product, so both are skipped.
"""

from __future__ import annotations

import urllib.parse
from typing import Callable

from ..catalog import AZURE
from ..ledger import observation_id
from . import Fetch
from .http import get_json

SOURCE = "azure"
API = "https://prices.azure.com/api/retail/prices"
MAX_PAGES = 50


def build_filter(skus: list[str]) -> str:
    any_sku = " or ".join(f"armSkuName eq '{s}'" for s in skus)
    return f"serviceName eq 'Virtual Machines' and priceType eq 'Consumption' and ({any_sku})"


def first_page_url() -> str:
    return API + "?" + urllib.parse.urlencode({"$filter": build_filter(sorted(AZURE))})


# kept under this name for backwards compatibility with existing callers and tests
http_get_json = get_json


def fetch_items(get: Callable[[str], dict] = http_get_json) -> list[dict]:
    items: list[dict] = []
    url: str | None = first_page_url()
    for _ in range(MAX_PAGES):
        if not url:
            return items
        page = get(url)
        items.extend(page.get("Items", []))
        url = page.get("NextPageLink")
    raise RuntimeError(f"Azure returned more than {MAX_PAGES} pages; refusing to continue")


def fetch(ledger=None) -> Fetch:
    return Fetch(items=fetch_items())


def tier_of(item: dict) -> str | None:
    meter = item.get("meterName", "")
    if "Low Priority" in meter:
        return None
    return "spot" if meter.endswith(" Spot") else "on_demand"


def parse(items: list[dict], observed_at: str) -> tuple[list[dict], dict[str, int]]:
    """Turn raw API rows into observations. Returns (observations, skipped-reason counts)."""
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    best: dict[tuple[str, str, str], dict] = {}
    for it in items:
        machine = AZURE.get(it.get("armSkuName", ""))
        if machine is None:
            skip("not in catalog"); continue
        if "Windows" in it.get("productName", ""):
            skip("windows"); continue
        tier = tier_of(it)
        if tier is None:
            skip("low priority"); continue
        if it.get("currencyCode") != "USD" or it.get("unitOfMeasure") != "1 Hour":
            skip("unexpected unit"); continue
        if not it.get("retailPrice") or it["retailPrice"] <= 0:
            skip("zero price"); continue
        key = (it["armSkuName"], it["armRegionName"], tier)
        # Azure sometimes lists the same machine twice in a region; keep the primary,
        # most recently effective meter.
        rank = (bool(it.get("isPrimaryMeterRegion")), it.get("effectiveStartDate", ""))
        if key in best:
            skip("duplicate meter")
            old = best[key]
            if rank <= (bool(old.get("isPrimaryMeterRegion")), old.get("effectiveStartDate", "")):
                continue
        best[key] = it

    observations = []
    for (sku, region, tier), it in sorted(best.items()):
        machine = AZURE[sku]
        price = float(it["retailPrice"])
        observations.append({
            "kind": "observation",
            "id": observation_id(SOURCE, sku, region, tier, observed_at),
            "observed_at": observed_at,
            "source": SOURCE,
            "gpu": machine.gpu,
            "gpu_model": machine.gpu_model,
            "gpu_count": machine.gpu_count,
            "instance": sku,
            "region": region,
            "tier": tier,
            "usd_per_hour": round(price, 6),
            "usd_per_gpu_hour": round(price / machine.gpu_count, 6),
            "source_ref": {
                "meter_id": it.get("meterId"),
                "sku_id": it.get("skuId"),
                "effective_from": it.get("effectiveStartDate"),
            },
        })
    return observations, skipped
