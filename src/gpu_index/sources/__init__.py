"""Price sources. Each one turns a public price feed into ledger observations.

Every source module provides:
  fetch(ledger) -> Fetch          get raw rows (or report that nothing changed)
  parse(items, observed_at)       turn raw rows into observations + skip counts
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Fetch:
    items: list[dict] = field(default_factory=list)
    unchanged: bool = False                     # nothing new published since the last run
    meta: dict = field(default_factory=dict)    # recorded on the run entry (e.g. a version)
