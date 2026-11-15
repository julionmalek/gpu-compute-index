from gpu_index.sources import azure

OBSERVED = "2026-11-15T06:00:00Z"


def row(sku, price, meter="", region="eastus", product="Virtual Machines NC Series", **extra):
    return {
        "armSkuName": sku, "retailPrice": price, "meterName": meter or sku.replace("Standard_", ""),
        "armRegionName": region, "productName": product, "currencyCode": "USD",
        "unitOfMeasure": "1 Hour", "isPrimaryMeterRegion": True,
        "effectiveStartDate": "2025-01-01T00:00:00Z", "meterId": "m", "skuId": "s", **extra,
    }


def test_normalises_to_price_per_gpu_hour():
    obs, _ = azure.parse([row("Standard_ND96isr_H100_v5", 98.32)], OBSERVED)
    [o] = obs
    assert (o["gpu"], o["gpu_count"], o["tier"]) == ("H100", 8, "on_demand")
    assert o["usd_per_gpu_hour"] == 12.29


def test_spot_low_priority_and_windows():
    obs, skipped = azure.parse([
        row("Standard_NC40ads_H100_v5", 2.0, meter="NC40ads H100 v5 Spot"),
        row("Standard_NC40ads_H100_v5", 3.0, meter="NC40ads H100 v5 Low Priority"),
        row("Standard_NC40ads_H100_v5", 9.0, product="Virtual Machines NCads H100 v5 Series Windows"),
    ], OBSERVED)
    assert [o["tier"] for o in obs] == ["spot"]
    assert skipped == {"low priority": 1, "windows": 1}


def test_ignores_machines_not_in_catalog():
    obs, skipped = azure.parse([row("Standard_NCC40ads_H100_v5", 7.0)], OBSERVED)
    assert obs == [] and skipped == {"not in catalog": 1}


def test_duplicate_meters_keep_primary_then_newest():
    obs, skipped = azure.parse([
        row("Standard_NC24ads_A100_v4", 4.0, isPrimaryMeterRegion=False),
        row("Standard_NC24ads_A100_v4", 3.67, effectiveStartDate="2026-01-01T00:00:00Z"),
        row("Standard_NC24ads_A100_v4", 3.50, effectiveStartDate="2024-01-01T00:00:00Z"),
    ], OBSERVED)
    assert [o["usd_per_hour"] for o in obs] == [3.67]
    assert skipped == {"duplicate meter": 2}


def test_ids_are_stable_for_the_same_snapshot():
    a, _ = azure.parse([row("Standard_NC24ads_A100_v4", 3.67)], OBSERVED)
    b, _ = azure.parse([row("Standard_NC24ads_A100_v4", 3.67)], OBSERVED)
    assert a[0]["id"] == b[0]["id"]


def test_pagination_follows_next_page_link():
    pages = {
        "first": {"Items": [row("Standard_NC24ads_A100_v4", 3.67)], "NextPageLink": "second"},
        "second": {"Items": [row("Standard_NC48ads_A100_v4", 7.35)], "NextPageLink": None},
    }
    calls = []

    def fake_get(url):
        calls.append(url)
        return pages["first" if url.startswith(azure.API) else url]

    items = azure.fetch_items(fake_get)
    assert len(items) == 2 and calls[1] == "second"


class _Resp:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, *a): return self.body


def _http_error(code, retry_after=None):
    import email.message
    import urllib.error
    headers = email.message.Message()
    if retry_after:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("u", code, "busy", headers, None)


def test_retries_when_rate_limited_then_succeeds():
    responses = [_http_error(429, "3"), _http_error(503), _Resp(b'{"Items": []}')]
    waits = []

    def opener(req, timeout):
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    assert azure.http_get_json("https://example.test/", sleep=waits.append, opener=opener) == {"Items": []}
    assert waits == [3.0, 10]  # Retry-After honoured, then exponential backoff


def test_gives_up_on_client_errors_immediately():
    import pytest

    def opener(req, timeout):
        raise _http_error(400)

    with pytest.raises(Exception):
        azure.http_get_json("https://example.test/", sleep=lambda s: None, opener=opener)


def test_filter_lists_every_catalog_sku():
    f = azure.build_filter(["Standard_A", "Standard_B"])
    assert "armSkuName eq 'Standard_A' or armSkuName eq 'Standard_B'" in f
