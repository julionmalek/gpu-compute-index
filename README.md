# GPU Compute Index

An open, auditable daily price index for renting GPU compute (H100, A100, RTX 4090).

Prices are collected from public sources into an **append-only, tamper-evident ledger** stored in this repo.
The index is computed deterministically from the ledger, so anyone can recompute and verify every published value.

> Work in progress. See the open pull requests.

## Latest values

USD per GPU-hour, 2026-11-15, from Azure and AWS list prices. One series per exact GPU model; rules in
[METHODOLOGY.md](METHODOLOGY.md).

| series | on-demand | spot | sources (on-demand) |
|--------|----------:|-----:|---------|
| H100 SXM 80GB | 12.12 | 2.95 | AWS, Azure |
| H100 NVL 94GB | 9.07 | 1.84 | Azure |
| A100 SXM 80GB | 4.72 | 1.45 | AWS, Azure |
| A100 PCIe 80GB | 4.78 | 0.93 | Azure |
| A100 SXM 40GB | 3.69 | 0.90 | AWS, Azure |

Spot prices are Azure only (AWS doesn't publish spot prices in its price list).

### History

The index goes back to **2021** (A100 SXM 40GB) and **2024** (H100 SXM), rebuilt from AWS's archived price lists
and dated by when AWS published each price. The clearest event so far: AWS's GPU price cut in mid-2025, visible in
the price list published on **2025-07-03**:

| series (AWS, on-demand) | before | after | change |
|---|---:|---:|---:|
| H100 SXM 80GB | 14.75 | 8.26 | −44% |
| A100 SXM 80GB | 6.15 | 4.12 | −33% |
| A100 SXM 40GB | 4.92 | 3.29 | −33% |

Machine-readable: [`index/latest.json`](index/latest.json) and one file per series in [`index/series/`](index/series/).

## Updates

A single GitHub Action ([`daily.yml`](.github/workflows/daily.yml)) runs every day at 06:17 UTC: it verifies the
ledger, collects Azure and AWS prices, verifies again, rebuilds the index and commits both. If a collection fails, the failure is
still committed as a `run` entry, so the gap is on the record, and the job is marked failed.

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
| AWS EC2 Price List (bulk files) | H100 SXM and A100 SXM list prices, on-demand, all regions | public, no key |

Which machines count, and how many GPUs each one has, is listed explicitly in
[`src/gpu_index/catalog.py`](src/gpu_index/catalog.py).

## Usage

Python 3.11+, no runtime dependencies.

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m gpu_index collect azure   # fetch prices and append them to the ledger
.venv/bin/python -m gpu_index collect aws     # only downloads (~6 GB, streamed) when AWS publishes a new version
.venv/bin/python -m gpu_index verify          # check the whole hash chain
.venv/bin/python -m gpu_index summary         # min / median / max per GPU, latest snapshot
.venv/bin/python -m gpu_index build           # recompute the index into ./index (refuses if verify fails)
.venv/bin/pytest
```
