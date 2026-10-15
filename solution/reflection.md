# Reflection (≤1 page)

**Which fault types were hardest to catch, and why?**

The checks pillar had the most fault types (6 in practice), and the most challenging were:
- **volume_spike**: Requires combining row_count elevation with staleness analysis. Some instances have high row_count but moderate staleness, needing a lower staleness threshold (3.0) or absolute row_count threshold (1.5× baseline).
- **null_spike**: Detecting when null_rate exceeds the 1% baseline limit. Relatively straightforward with the hard baseline check.
- **freshness_lag**: Staleness exceeding the 8.418-minute max. Caught by comparing staleness_min against the calibrated baseline.
- **distribution_shift**: mean_amount outside [70, 95] range. Caught by simple range check.

The ai_infra and lineage pillars were easier — feature skew and embedding drift have clear sigma-based thresholds, and lineage anomalies (orphan outputs, missing upstream edges, runtime duration) are binary conditions that are either true or false.

**What would you change about your cost/coverage tradeoff, if you had another pass?**

The current design makes full use of the 220-credit practice budget (~1 metered call per event on average, costing 180 credits). All 5 pillar detectors are active, which maximizes TPR but also incurs the most calls. 

Key observations:
- ai_infra (feature_drift at 2.0 credits) and embedding_drift (2.0 credits) are the most expensive calls. The current memoization by (feature_view, batch_id) and (corpus, chunk_batch_id) prevents redundant calls within a run.
- checks (batch_profile at 1.0 credit) is the cheapest and most frequently called. The volume_spike condition with the dual threshold (staleness > 3.0 OR row_count > 1.5×max) catches all volume fault types without significantly increasing FPR.
- contracts and lineage are moderately priced (1.5 and 1.0 credits respectively) and have 100% TPR in practice, indicating their fault signals are well-conditioned.

If I had another pass, I would explore whether the lineage "missing_upstream" detection could be improved by checking for partial upstream matches rather than exact set equality, and whether the null_rate check could be made more sensitive to subtle spikes near the 1% boundary without tanking FPR.
