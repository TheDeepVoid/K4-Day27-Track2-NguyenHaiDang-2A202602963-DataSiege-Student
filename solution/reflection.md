# Reflection (≤1 page)

## Which fault types were hardest to catch, and why?

The most challenging faults were in the **checks** pillar, specifically:

1. **volume_spike** (obvious tier): Requires combining row_count elevation with staleness analysis. Some instances have very high row_count (800-876 vs baseline max 561) but moderate staleness (1.13-4.30 vs baseline max 8.42). The detection needed a dual-threshold approach: row_count > 1.3×max AND staleness > 3.0, OR row_count > 1.5×max alone.

2. **null_spike** (obvious tier): Straightforward - null_rate exceeds the 1.09% baseline max. The event 80 had 3.18% null rate which clearly triggers.

3. **freshness_lag** (obvious tier): Staleness_min exceeds the 8.42 minute baseline. Event 10 with 20.42 minutes clearly triggers.

4. **distribution_shift** (obvious tier): mean_amount outside [72.76, 90.61] range. Event 15 with 112.64 clearly triggers.

The **contracts** pillar faults were easier to detect:
- **schema_break**: contract_diff directly reports schema_hash_mismatch
- **type_violation**: contract_diff reports amount type not being float

The **lineage** pillar faults were binary conditions:
- **missing_upstream**: actual upstream set ≠ expected {raw.orders, raw.customers}
- **orphan_output**: downstream_count == 0
- **runtime_anomaly**: duration_ms > 5135ms baseline max

The **ai_infra** pillar had clear statistical thresholds:
- **feature_skew**: mean_shift_sigma > 0.41 baseline max (or subtle: >0.25 with large serve-train gap)
- **embedding_drift**: centroid_shift > 0.0435 baseline max
- **corpus_staleness**: avg_doc_age_days > 49.8 baseline max

## What would you change about your cost/coverage tradeoff, if you had another pass?

The current implementation uses ~180 credits on practice (220 budget) and ~240 on public (220 budget, slight overage). Key observations:

1. **Feature drift (2.0 credits) and embedding drift (2.0 credits)** are the most expensive calls. Memoization prevents redundant calls, but there's room to skip these for events with clear other signals.

2. **The volume_spike dual-threshold** works well but the staleness > 3.0 threshold was tuned on practice data. A more robust approach would use statistical hypothesis testing on row_count distribution.

3. **False positive rate (9.2%)** comes mostly from clean events near threshold boundaries. Could improve by:
   - Using exponentially weighted moving averages instead of static baselines
   - Implementing confidence intervals for each metric
   - Adding cross-pillar correlation (e.g., volume spike + contract freshness lag = higher confidence)

4. **Cost optimization**: Could skip feature_drift/embedding_drift calls when other pillars already indicate clear faults, saving ~4 credits per event.

5. **Private phase preparation**: The private phase has a larger budget (320) but harder faults. The current thresholds may need adjustment for subtle-magnitude faults that sit closer to normal variance.
