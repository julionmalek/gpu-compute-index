import json
from datetime import datetime, timezone

import pytest

from gpu_index import index
from gpu_index.ledger import Ledger, observation_id


def o(price, region="r1", source="azure", instance="box", gpu="H100", tier="on_demand",
      observed="2026-11-15T06:00:00Z", model="H100 SXM 80GB"):
    return {
        "kind": "observation",
        "id": observation_id(source, instance, region, tier, observed),
        "observed_at": observed, "source": source, "gpu": gpu, "gpu_model": model, "gpu_count": 1,
        "instance": instance, "region": region, "tier": tier,
        "usd_per_hour": price, "usd_per_gpu_hour": price, "source_ref": {},
    }


# ---------- building blocks ----------

def test_weighted_quantile():
    assert index.weighted_quantile([1, 2, 3], [1, 1, 1], 0.5) == 2
    assert index.weighted_quantile([1, 2, 3], [1, 1, 10], 0.5) == 3
    assert index.weighted_quantile([5], [1], 0.25) == 5
    with pytest.raises(ValueError):
        index.weighted_quantile([], [], 0.5)


def test_nested_weights_stop_one_region_from_dominating():
    # region r1 lists 4 machine sizes, r2 lists 1: each region still gets half the weight
    obs = [o(1, "r1", instance=f"m{i}") for i in range(4)] + [o(9, "r2")]
    w = index.nested_weights(obs)
    assert w[:4] == [0.125] * 4 and w[4] == 0.5
    assert sum(w) == pytest.approx(1.0)


def test_nested_weights_give_each_source_an_equal_share():
    obs = [o(1, f"r{i}") for i in range(9)] + [o(2, "x", source="vast")]
    w = index.nested_weights(obs)
    assert sum(w[:9]) == pytest.approx(0.5) and w[9] == pytest.approx(0.5)


def test_outliers_are_prices_3x_away_from_the_median():
    obs = [o(p, f"r{i}") for i, p in enumerate([2.0, 2.1, 2.2, 2.3, 2.4, 2.5, 50.0, 0.05])]
    kept, dropped = index.drop_outliers(obs)
    assert sorted(x["usd_per_gpu_hour"] for x in dropped) == [0.05, 50.0]
    assert len(kept) == 6


def test_regional_premiums_are_kept():
    # Brazil-style premium (+55%) is real pricing, not an error
    kept, dropped = index.drop_outliers([o(p, f"r{i}") for i, p in enumerate([4.7, 4.8, 4.8, 4.9, 7.3])])
    assert dropped == []


def test_different_models_are_separate_series():
    # NVL around $9 and SXM around $16 must not be pooled (pooling made SXM look like outliers)
    nvl = [o(9.0 + i / 10, f"r{i}", instance="nvl", model="H100 NVL 94GB") for i in range(5)]
    sxm = [o(16.0 + i / 10, f"r{i}", instance="sxm", model="H100 SXM 80GB") for i in range(5)]
    series = index.compute_series(nvl + sxm)
    assert series[("H100", "H100 NVL 94GB", "on_demand")][0].value == 9.2
    assert series[("H100", "H100 SXM 80GB", "on_demand")][0].value == 16.2
    assert index.slug("H100 SXM 80GB", "on_demand") == "h100-sxm-80gb-on-demand"


def test_identical_prices_are_never_dropped():
    kept, dropped = index.drop_outliers([o(3.0, f"r{i}") for i in range(6)])
    assert len(kept) == 6 and dropped == []


def test_only_each_sources_last_snapshot_of_the_day_counts():
    early = [o(1.0, f"r{i}", observed="2026-11-15T01:00:00Z") for i in range(5)]
    late = [o(2.0, f"r{i}", observed="2026-11-15T20:00:00Z") for i in range(5)]
    days = index.daily_snapshots(early + late)
    assert {x["usd_per_gpu_hour"] for x in days["2026-11-15"]} == {2.0}


def test_list_prices_carry_forward_until_replaced():
    # AWS-style: one snapshot on the 1st; Azure-style: daily snapshots
    aws = [o(7.0, f"a{i}", source="aws", observed="2026-11-01T06:00:00Z") for i in range(5)]
    azure = [o(9.0, f"z{i}", source="azure", observed=f"2026-11-{d:02d}T06:00:00Z") for d in (1, 2, 3) for i in range(5)]
    days = index.daily_snapshots(aws + azure)
    assert sorted(days) == ["2026-11-01", "2026-11-02", "2026-11-03"]
    on_3rd = days["2026-11-03"]
    assert {x["source"] for x in on_3rd} == {"aws", "azure"}
    assert {x["observed_at"] for x in on_3rd if x["source"] == "azure"} == {"2026-11-03T06:00:00Z"}
    point = index.compute_point("2026-11-03", on_3rd)
    assert point.source_dates == {"aws": "2026-11-01", "azure": "2026-11-03"}


def test_stale_snapshots_expire():
    old = [o(7.0, f"a{i}", source="aws", observed="2026-09-01T06:00:00Z") for i in range(5)]
    fresh = [o(9.0, f"z{i}", source="azure", observed="2026-11-15T06:00:00Z") for i in range(5)]
    days = index.daily_snapshots(old + fresh)
    assert {x["source"] for x in days["2026-10-06"]} == {"aws"}       # exactly 35 days: still counts
    assert days["2026-10-07"] == []                                     # 36 days: expired, and no azure yet
    assert {x["source"] for x in days["2026-11-15"]} == {"azure"}


def test_gaps_between_snapshots_are_filled_day_by_day():
    a = [o(2.0, f"r{i}", observed="2026-11-01T06:00:00Z") for i in range(5)]
    b = [o(3.0, f"r{i}", observed="2026-11-04T06:00:00Z") for i in range(5)]
    days = index.daily_snapshots(a + b)
    assert [days[d][0]["usd_per_gpu_hour"] for d in sorted(days)] == [2.0, 2.0, 2.0, 3.0]


def test_no_value_when_too_few_prices():
    assert index.compute_point("2026-11-15", [o(2.0, f"r{i}") for i in range(4)]) is None
    point = index.compute_point("2026-11-15", [o(2.0 + i / 10, f"r{i}") for i in range(5)])
    assert point.value == 2.2 and point.used == 5


# ---------- end to end, through the ledger ----------

@pytest.fixture
def ledger(tmp_path):
    lg = Ledger(tmp_path / "ledger")
    day1 = [o(2.0 + i / 10, f"r{i}", observed="2026-11-15T06:00:00Z") for i in range(6)]
    day2 = [o(3.0 + i / 10, f"r{i}", observed="2026-11-16T06:00:00Z") for i in range(6)]
    lg.append(day1, at=datetime(2026, 11, 15, 6, tzinfo=timezone.utc))
    lg.append(day2, at=datetime(2026, 11, 16, 6, tzinfo=timezone.utc))
    return lg


def test_build_writes_series_and_latest(ledger, tmp_path):
    latest = index.build(ledger, tmp_path / "out")
    series = json.loads((tmp_path / "out/series/h100-sxm-80gb-on-demand.json").read_text())
    assert [p["date"] for p in series["points"]] == ["2026-11-15", "2026-11-16"]
    assert latest["series"]["h100-sxm-80gb-on-demand"]["date"] == "2026-11-16"
    assert latest["ledger_head"] == ledger.head()[0]
    assert latest["methodology_version"] == index.METHODOLOGY_VERSION


def test_build_is_deterministic(ledger, tmp_path):
    index.build(ledger, tmp_path / "a")
    index.build(ledger, tmp_path / "b")
    for f in ["latest.json", "series/h100-sxm-80gb-on-demand.json"]:
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()


def test_a_correction_restates_the_published_value(ledger, tmp_path):
    before = index.build(ledger, tmp_path / "out")["series"]["h100-sxm-80gb-on-demand"]
    target = [x for x in ledger.observations() if x["observed_at"].startswith("2026-11-16")]
    for x in target:
        ledger.correct(x["id"], "test restatement", set_fields={"usd_per_gpu_hour": x["usd_per_gpu_hour"] * 2},
                       at=datetime(2026, 11, 16, 9, tzinfo=timezone.utc))
    after = index.build(ledger, tmp_path / "out")["series"]["h100-sxm-80gb-on-demand"]
    assert after["value"] == pytest.approx(before["value"] * 2)
