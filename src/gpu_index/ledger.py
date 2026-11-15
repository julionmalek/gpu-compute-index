"""An append-only, tamper-evident ledger stored as plain JSON Lines files.

Layout:  ledger/YYYY/MM/DD.jsonl  (one file per UTC day, never rewritten)

Every entry carries the hash of the entry before it, forming one chain across all
files. Editing, deleting or reordering any past line breaks the chain, and
`Ledger.verify()` reports exactly where. Mistakes are never fixed in place: a
"correction" entry is appended that points at the observation it amends.

Entry kinds:
  observation  one price seen at one moment (the raw facts)
  correction   amends or voids an earlier observation, with a reason
  run          one collection attempt, including failures, so gaps are explainable
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = 1
GENESIS = "0" * 64
KINDS = {"observation", "correction", "run"}


def canonical(obj: Any) -> bytes:
    """Stable byte encoding used for hashing (key order and spacing never vary)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(canonical(body)).hexdigest()


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def observation_id(source: str, instance: str, region: str, tier: str, observed_at: str) -> str:
    key = "|".join([source, instance, region, tier, observed_at])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


@dataclass
class VerifyResult:
    entries: int = 0
    files: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


class LedgerError(Exception):
    pass


class Ledger:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    # ---------- reading ----------
    def files(self) -> list[Path]:
        # YYYY/MM/DD sorts correctly as plain strings
        return sorted(self.root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9].jsonl"))

    def entries(self) -> Iterator[dict]:
        for path in self.files():
            with path.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        yield json.loads(line)

    def head(self) -> tuple[str, str | None]:
        """Hash and timestamp of the newest entry (GENESIS if the ledger is empty)."""
        files = self.files()
        if not files:
            return GENESIS, None
        last = None
        with files[-1].open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        if last is None:
            return GENESIS, None
        e = json.loads(last)
        return e["hash"], e["recorded_at"]

    # ---------- writing ----------
    def append(self, bodies: Iterable[dict], at: datetime | None = None) -> list[dict]:
        """Append entries recorded at `at` (default: now). Returns the written entries.

        Observations whose id is already in the ledger are skipped, so re-running a
        collection for the same moment is harmless.
        """
        at = at or datetime.now(timezone.utc)
        recorded_at = iso(at)
        prev, last_at = self.head()
        if last_at and recorded_at < last_at:
            raise LedgerError(f"refusing to write {recorded_at}: ledger already has entries up to {last_at}")

        seen = {e["id"] for e in self.entries() if e.get("kind") == "observation"}
        written: list[dict] = []
        for body in bodies:
            kind = body.get("kind")
            if kind not in KINDS:
                raise LedgerError(f"unknown entry kind: {kind!r}")
            if kind == "observation":
                if body["id"] in seen:
                    continue
                seen.add(body["id"])
            entry = {**body, "schema_version": SCHEMA_VERSION, "recorded_at": recorded_at, "prev_hash": prev}
            entry["hash"] = entry_hash(entry)
            prev = entry["hash"]
            written.append(entry)

        if written:
            path = self.root / at.strftime("%Y/%m/%d.jsonl")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                for entry in written:
                    f.write(canonical(entry).decode() + "\n")
        return written

    def correct(self, target_id: str, reason: str, *, set_fields: dict | None = None,
                void: bool = False, at: datetime | None = None) -> dict:
        if not reason.strip():
            raise LedgerError("a correction needs a reason")
        if not void and not set_fields:
            raise LedgerError("a correction must either void the observation or set fields")
        if target_id not in {e["id"] for e in self.entries() if e.get("kind") == "observation"}:
            raise LedgerError(f"no observation with id {target_id}")
        body = {"kind": "correction", "target": target_id, "reason": reason, "void": void, "set": set_fields or {}}
        return self.append([body], at=at)[0]

    # ---------- checking ----------
    def verify(self) -> VerifyResult:
        result = VerifyResult()
        prev = GENESIS
        last_at = ""
        for path in self.files():
            result.files += 1
            with path.open(encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    if not line.strip():
                        continue
                    where = f"{path.relative_to(self.root)}:{n}"
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        result.problems.append(f"{where}: not valid JSON")
                        continue
                    result.entries += 1
                    if e.get("prev_hash") != prev:
                        result.problems.append(f"{where}: chain broken (prev_hash does not match the entry before)")
                    if entry_hash(e) != e.get("hash"):
                        result.problems.append(f"{where}: contents changed after it was written (hash mismatch)")
                    if e.get("recorded_at", "") < last_at:
                        result.problems.append(f"{where}: out of order in time")
                    if not e.get("recorded_at", "").startswith(path.parent.parent.name + "-" + path.parent.name + "-" + path.stem):
                        result.problems.append(f"{where}: stored in the wrong day file")
                    prev = e.get("hash", "")
                    last_at = e.get("recorded_at", last_at)
        return result

    # ---------- the corrected view ----------
    def observations(self) -> list[dict]:
        """All observations with corrections applied (voided ones removed)."""
        obs: dict[str, dict] = {}
        for e in self.entries():
            if e["kind"] == "observation":
                obs[e["id"]] = dict(e)
            elif e["kind"] == "correction" and e["target"] in obs:
                if e["void"]:
                    obs.pop(e["target"])
                else:
                    o = obs[e["target"]]
                    o.update(e["set"])
                    o.setdefault("corrections", []).append(e["hash"])
        return list(obs.values())
