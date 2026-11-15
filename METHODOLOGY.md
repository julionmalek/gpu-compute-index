# Index methodology

**Version 1.1** · applies to every value in `index/`

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

1. **Snapshot.** Each source contributes **one** snapshot: the latest one it took **on or before that day**,
   as long as it is **at most 35 days old**.
   - A source collected several times in a day counts once (its last snapshot).
   - Sources that update daily (Azure) count with that day's prices. List prices that change rarely
     (AWS publishes a new price list about once a month) stay valid until replaced.
   - After 35 days without a new snapshot, a source drops out instead of propping up the index with stale prices.
   - There is a value for every calendar day between the first and the last snapshot.
2. **Outliers.** Prices more than **3× above or below the median** of that day's prices are dropped.
   This is meant to catch data errors (a whole-machine price read as per-GPU, a misplaced decimal),
   not to remove real differences: a region that is 50% more expensive stays in.
3. **Weights within a source.** Every **region** gets the same weight, and within a region every **machine size**
   gets the same weight. So a region that lists four machine sizes does not count four times as much as a region
   that lists one.
4. **Value.** Two steps:
   1. each source's **weighted median** price;
   2. the **plain average of those medians**, so every source counts equally.

   Why not one median over all prices? Different providers price the same GPU in separate clusters (for the
   H100 SXM, AWS lists about $7–9 and Azure about $16). A single pooled median lands on whichever price happens to
   sit at the boundary between the clusters (on 2026-11-15 it was AWS's single most expensive region) and
   jumps when one listing changes. Averaging per-source medians is stable and easy to check by hand.

   The **25th and 75th percentiles** over all prices (each source weighted equally) are published alongside as
   the typical range.
5. **Minimum data.** A source needs at least **3 prices** that day to count, so one or two listings can't swing
   half the index. If fewer than **5 prices** remain in total, **no value is published** for that day.
   A missing value is better than a misleading one.

The weighted quantile at level *q* is the smallest price whose cumulative weight (prices sorted ascending)
reaches *q* of the total weight. Values are rounded to 4 decimal places.

## History (backfill)

Values before the daily collection started come from **AWS's archived price lists**, which AWS keeps publicly,
back to 2015. The backfill starts in November 2020, when the A100 launched on AWS.

- **Point-in-time dating.** Each archived price is dated by when AWS **published** that price list (the version
  id is the publication timestamp), not by the month it was "effective from". AWS often publishes a month's list
  weeks after it takes effect, so this is conservative: the index never uses a price before the market could
  have seen it. That makes the history safe for backtesting.
- **Same rules.** Historical days follow exactly the same calculation as today, including carry-forward
  (AWS publishes about monthly) and the minimums: a series has no value until at least 5 prices exist, which for
  new GPUs means until AWS offered them in enough regions.
- **AWS only.** Azure has no public price archive, so historical values have one source; `sources` and
  `source_dates` on each value show this.
- **Auditable.** Every backfilled price records the price list version it came from (`source_ref`), and every
  version collected has a `run` entry marked `backfill`. Anyone can download that exact archived file and check.

## Published files

| file | contents |
|------|----------|
| `index/series/<series>.json` | every daily value for one series |
| `index/latest.json` | the latest value of every series, the methodology version, and the ledger head hash |

Each daily value records how many prices were used, how many were dropped as outliers, how many came from
each source, and **the date each source's prices were taken** (`source_dates`), so carried-forward prices are
always visible.

## Reproducibility and restatements

The index is a pure function of the ledger: rebuilding from the same ledger always produces byte-identical files
(this is tested). `latest.json` includes `ledger_head`, the hash of the newest ledger entry used, so anyone can
check out that ledger state and reproduce every number.

If a correction is later appended to the ledger, rebuilding **restates** the affected days. Because the published
files are committed to git, every restatement is visible as a diff, with the correction's reason in the ledger.

## Known limitations (v1.1)

- **Two sources, both hyperscalers.** Azure and AWS list prices. Specialist GPU clouds and marketplaces (where the
  RTX 4090 trades) are cheaper but need a source whose terms allow automated collection.
- **AWS has no spot prices** in its public price list, so spot series are Azure only.
- **Level jumps when a source joins.** The value averages the sources present, so when a source enters or leaves
  a series, the level can jump without any price changing. On 2026-11-15 the H100 SXM series went from 8.26
  (AWS only) to 12.12 (AWS + Azure) because Azure's daily collection started, not because prices moved.
  Read changes across such dates using `sources`, or compare per-source values. A later version should
  chain-link (scale the new composition so the level is continuous on the day it changes), as commodity
  indices do when their constituents change.
- **List prices, not traded prices.** These are advertised prices. Actual deals, discounts and committed-use
  contracts are usually cheaper.
- **No volume weighting.** Providers don't publish how much capacity they have at each price, so every region
  is weighted equally rather than by size.

## Changelog

- **1.1** Sources contribute their latest snapshot up to 35 days old (was: only snapshots taken that day),
  so monthly list prices such as AWS's carry forward. Each value records `source_dates`.
  The value is the average of per-source medians (was: one median over all prices), and a source needs at
  least 3 prices to count.
- **1.0** First version.
