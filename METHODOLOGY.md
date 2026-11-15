# Index methodology

**Version 1.0** · applies to every value in `index/`

This document is the contract. The code in [`src/gpu_index/index.py`](src/gpu_index/index.py) implements exactly
these rules, and any change to them gets a new version number.

## What is measured

The price, in **US dollars per GPU-hour**, of renting one GPU of a specific model, for one hour, from a public
provider, with no commitment.

There is **one series per exact GPU model and pricing tier**, for example `h100-sxm-80gb-on-demand`.

- **Why per model?** An H100 SXM (the version used in large training clusters) and an H100 NVL are different
  products at very different prices. A single "H100" number would move whenever the mix of listings changed,
  even if no price did.
- **Tiers.** `on-demand` is the standard pay-as-you-go price. `spot` is spare capacity that the provider can
  reclaim at short notice; it is much cheaper and is published as its own series.

## Inputs

Only observations in the [ledger](README.md#how-the-ledger-works), with all corrections applied. Voided
observations are excluded. Nothing outside the ledger is used.

## Daily calculation

For each series and each UTC day:

1. **Snapshot.** If a source was collected several times that day, only its **last** snapshot counts.
   This stops a source from counting double because it was collected twice.
2. **Outliers.** Prices more than **3× above or below the median** of that day's prices are dropped.
   This is meant to catch data errors (a whole-machine price read as per-GPU, a misplaced decimal),
   not to remove real differences: a region that is 50% more expensive stays in.
3. **Weights.** Equal shares at each level:
   - every **source** gets the same total weight,
   - within a source, every **region** gets the same weight,
   - within a region, every **machine size** gets the same weight.

   So a region that lists four machine sizes does not count four times as much as a region that lists one.
4. **Value.** The published value is the **weighted median** of the remaining prices.
   The weighted **25th and 75th percentiles** are published alongside it as the typical range.
5. **Minimum data.** If fewer than **5 prices** remain, **no value is published** for that day.
   A missing value is better than a misleading one.

The weighted quantile at level *q* is the smallest price whose cumulative weight (prices sorted ascending)
reaches *q* of the total weight. Values are rounded to 4 decimal places.

## Published files

| file | contents |
|------|----------|
| `index/series/<series>.json` | every daily value for one series |
| `index/latest.json` | the latest value of every series, the methodology version, and the ledger head hash |

Each daily value records how many prices were used, how many were dropped as outliers, and how many came from
each source.

## Reproducibility and restatements

The index is a pure function of the ledger: rebuilding from the same ledger always produces byte-identical files
(this is tested). `latest.json` includes `ledger_head`, the hash of the newest ledger entry used, so anyone can
check out that ledger state and reproduce every number.

If a correction is later appended to the ledger, rebuilding **restates** the affected days. Because the published
files are committed to git, every restatement is visible as a diff, with the correction's reason in the ledger.

## Known limitations (v1.0)

- **One source.** Only Azure list prices so far. Marketplace sources (where the RTX 4090 trades) come next.
- **List prices, not traded prices.** These are advertised prices. Actual deals, discounts and committed-use
  contracts are usually cheaper.
- **No volume weighting.** Providers don't publish how much capacity they have at each price, so every region
  is weighted equally rather than by size.

## Changelog

- **1.0** First version.
