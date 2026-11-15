import csv
import io
from datetime import datetime, timezone

from gpu_index import cli
from gpu_index.ledger import Ledger
from gpu_index.sources import Fetch, aws

OBSERVED = "2026-11-15T06:00:00Z"
COLUMNS = ["SKU", "RateCode", "TermType", "EffectiveDate", "Unit", "PricePerUnit", "Currency", "Instance Type",
           "Tenancy", "Operating System", "Pre Installed S/W", "CapacityStatus", "MarketOption", "operation", "Region Code"]


def row(instance="p5.48xlarge", price="55.04", region="us-east-1", **over):
    r = {"SKU": "S1", "RateCode": "S1.A.B", "TermType": "OnDemand", "EffectiveDate": "2026-11-01", "Unit": "Hrs",
         "PricePerUnit": price, "Currency": "USD", "Instance Type": instance, "Tenancy": "Shared",
         "Operating System": "Linux", "Pre Installed S/W": "NA", "CapacityStatus": "Used",
         "MarketOption": "OnDemand", "operation": "RunInstances", "Region Code": region}
    r.update(over)
    return r


def csv_lines(rows):
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    for k, v in [("FormatVersion", "v1.0"), ("Disclaimer", "x"), ("Publication Date", "2026-11-14T01:29:26Z"),
                 ("Version", "20261114012926"), ("OfferCode", "AmazonEC2")]:
        w.writerow([k, v])
    w.writerow(COLUMNS)
    for r in rows:
        w.writerow([r[c] for c in COLUMNS])
    return [line.encode() + b"\n" for line in buf.getvalue().splitlines()]


def test_streaming_keeps_only_catalog_machines_and_reads_version():
    lines = csv_lines([row(), row(instance="p5en.48xlarge"), row(instance="m5.large", price="0.096")])
    rows = list(aws.gpu_rows(lines))
    assert [r["Instance Type"] for r in rows] == ["p5.48xlarge"]  # p5en (H200) must not match p5
    assert rows[0]["_version"] == "20261114012926"


def test_parse_normalises_and_filters():
    items = [
        row(),                                                       # kept: 55.04 / 8
        row(price="0", MarketOption="CapacityBlock", operation="RunInstances:CB"),
        row(**{"Operating System": "RHEL"}),
        row(Tenancy="Dedicated"),
        row(CapacityStatus="UnusedCapacityReservation"),
        row(TermType="Reserved"),
        row(instance="p4d.24xlarge", price="21.957642", region="us-west-2"),
    ]
    obs, skipped = aws.parse(items, OBSERVED)
    by_instance = {o["instance"]: o for o in obs}
    assert by_instance["p5.48xlarge"]["usd_per_gpu_hour"] == 6.88
    assert by_instance["p5.48xlarge"]["gpu_model"] == "H100 SXM 80GB"
    assert by_instance["p4d.24xlarge"]["gpu_model"] == "A100 SXM 40GB"
    assert all(o["tier"] == "on_demand" for o in obs)
    assert skipped == {"capacity block": 1, "os or software licence": 1, "dedicated or reservation": 2, "not on-demand": 1}


def test_one_and_eight_gpu_machines_agree_per_gpu():
    obs, _ = aws.parse([row(), row(instance="p5.4xlarge", price="6.88")], OBSERVED)
    assert {o["usd_per_gpu_hour"] for o in obs} == {6.88}


def test_unchanged_version_is_recorded_without_downloading(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "ledger")
    ledger.append([{"kind": "run", "run_id": "aws-1", "source": "aws", "status": "ok", "version": "V1"}],
                  at=datetime(2026, 11, 14, tzinfo=timezone.utc))
    monkeypatch.setattr(aws, "current_version", lambda: "V1")
    monkeypatch.setattr(aws, "fetch_items", lambda version: (_ for _ in ()).throw(AssertionError("should not download")))
    assert aws.fetch(ledger) == Fetch(unchanged=True, meta={"version": "V1"})

    assert cli.collect(ledger, "aws") == 0
    last = list(ledger.entries())[-1]
    assert last["kind"] == "run" and last["unchanged"] is True and last["version"] == "V1"
    assert ledger.verify().ok


def test_new_version_is_downloaded(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "ledger")
    monkeypatch.setattr(aws, "current_version", lambda: "V2")
    monkeypatch.setattr(aws, "fetch_items", lambda version: [row()])
    assert cli.collect(ledger, "aws") == 0
    entries = list(ledger.entries())
    assert [e["kind"] for e in entries] == ["observation", "run"]
    assert entries[-1]["version"] == "V2" and "unchanged" not in entries[-1]
    assert aws.last_version(ledger) == "V2"


def test_failed_runs_do_not_count_as_collected(tmp_path):
    ledger = Ledger(tmp_path / "ledger")
    ledger.append([{"kind": "run", "run_id": "aws-1", "source": "aws", "status": "error", "version": "V3"}],
                  at=datetime(2026, 11, 14, tzinfo=timezone.utc))
    assert aws.last_version(ledger) is None


def test_region_urls_point_at_csv_files():
    def get(url):
        assert url.endswith("/V9/region_index.json")
        return {"regions": {"us-east-1": {"currentVersionUrl": "/offers/v1.0/aws/AmazonEC2/V9/us-east-1/index.json"}}}
    assert aws.region_csv_urls("V9", get) == ["https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/V9/us-east-1/index.csv"]
