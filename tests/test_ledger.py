from datetime import datetime, timezone

import pytest

from gpu_index.ledger import GENESIS, Ledger, LedgerError, observation_id


def at(day, hour=6):
    return datetime(2026, 11, day, hour, 0, tzinfo=timezone.utc)


def obs(price, region="eastus", observed="2026-11-15T06:00:00Z"):
    return {
        "kind": "observation",
        "id": observation_id("test", "box", region, "on_demand", observed),
        "observed_at": observed, "source": "test", "gpu": "H100", "gpu_model": "H100 SXM 80GB",
        "gpu_count": 1, "instance": "box", "region": region, "tier": "on_demand",
        "usd_per_hour": price, "usd_per_gpu_hour": price, "source_ref": {},
    }


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger")


def test_append_writes_one_file_per_day_and_chains(ledger):
    first = ledger.append([obs(2.0)], at=at(15))
    second = ledger.append([obs(2.5, region="westus", observed="2026-11-16T06:00:00Z")], at=at(16))
    assert first[0]["prev_hash"] == GENESIS
    assert second[0]["prev_hash"] == first[0]["hash"]
    assert [p.relative_to(ledger.root).as_posix() for p in ledger.files()] == ["2026/11/15.jsonl", "2026/11/16.jsonl"]
    assert ledger.verify().ok


def test_rerunning_the_same_snapshot_is_harmless(ledger):
    ledger.append([obs(2.0)], at=at(15))
    again = ledger.append([obs(2.0)], at=at(15, 7))
    assert again == []
    assert len(ledger.observations()) == 1


def test_editing_a_past_line_is_detected(ledger):
    ledger.append([obs(2.0), obs(3.0, region="westus")], at=at(15))
    path = ledger.files()[0]
    path.write_text(path.read_text().replace('"usd_per_hour":2.0', '"usd_per_hour":1.0'))
    result = ledger.verify()
    assert not result.ok
    assert any("hash mismatch" in p for p in result.problems)


def test_deleting_a_past_line_is_detected(ledger):
    ledger.append([obs(2.0), obs(3.0, region="westus"), obs(4.0, region="northeurope")], at=at(15))
    path = ledger.files()[0]
    lines = path.read_text().splitlines(keepends=True)
    path.write_text(lines[0] + lines[2])
    assert any("chain broken" in p for p in ledger.verify().problems)


def test_cannot_write_into_the_past(ledger):
    ledger.append([obs(2.0)], at=at(16))
    with pytest.raises(LedgerError):
        ledger.append([obs(3.0, observed="2026-11-15T06:00:00Z", region="x")], at=at(15))


def test_corrections_amend_without_rewriting_history(ledger):
    [o] = ledger.append([obs(20.0)], at=at(15))
    ledger.correct(o["id"], "source listed price per 8 GPUs, not per GPU", set_fields={"usd_per_gpu_hour": 2.5}, at=at(15, 9))
    [view] = ledger.observations()
    assert view["usd_per_gpu_hour"] == 2.5
    assert len(view["corrections"]) == 1
    # the original line is untouched and the chain still verifies
    assert '"usd_per_gpu_hour":20.0' in ledger.files()[0].read_text()
    assert ledger.verify().ok


def test_void_correction_removes_observation_from_view(ledger):
    [o] = ledger.append([obs(0.01)], at=at(15))
    ledger.correct(o["id"], "obvious pricing error at source", void=True, at=at(15, 9))
    assert ledger.observations() == []


def test_correction_rules(ledger):
    [o] = ledger.append([obs(2.0)], at=at(15))
    with pytest.raises(LedgerError):
        ledger.correct(o["id"], "  ", void=True, at=at(15, 9))
    with pytest.raises(LedgerError):
        ledger.correct(o["id"], "no change given", at=at(15, 9))
    with pytest.raises(LedgerError):
        ledger.correct("doesnotexist", "typo", void=True, at=at(15, 9))
