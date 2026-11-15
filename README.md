# GPU Compute Index

An open, auditable daily price index for renting GPU compute (H100, A100, RTX 4090).

Prices are collected from public sources into an **append-only, tamper-evident ledger** stored in this repo.
The index will be computed deterministically from the ledger, so anyone can recompute and verify every published value.

> Work in progress. See the open pull requests.

## How the ledger works

```
ledger/
  2026/11/15.jsonl   one file per UTC day, only ever appended to
```

Every line is one entry, and every entry stores the SHA-256 hash of the entry before it. That makes one chain
across all files: editing, deleting or reordering any past line breaks it, and `verify` points at the exact line.

| kind          | what it records                                                                          |
|---------------|------------------------------------------------------------------------------------------|
| `observation` | one price, from one source, for one machine, in one region, at one moment                |
| `correction`  | amends or voids an earlier observation, with a written reason (history is never edited)  |
| `run`         | one collection attempt, **including failures**, so any gap in the data is explained      |

All prices are normalised to **USD per GPU-hour** (an 8x H100 machine at $98.32/h is $12.29 per GPU-hour).

## Sources

| source                  | what                                                       | access          |
|-------------------------|------------------------------------------------------------|-----------------|
| Azure Retail Prices API | H100 and A100 list prices, on-demand and spot, all regions | public, no key  |

Which machines count, and how many GPUs each one has, is listed explicitly in
[`src/gpu_index/catalog.py`](src/gpu_index/catalog.py).

## Usage

Python 3.11+, no runtime dependencies.

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m gpu_index collect azure   # fetch prices and append them to the ledger
.venv/bin/python -m gpu_index verify          # check the whole hash chain
.venv/bin/python -m gpu_index summary         # min / median / max per GPU, latest snapshot
.venv/bin/pytest
```
