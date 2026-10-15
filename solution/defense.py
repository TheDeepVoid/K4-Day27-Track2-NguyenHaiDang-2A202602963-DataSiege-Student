"""
Data Siege defense — pipeline fault detection.

Implements register(ctx) and a handler per event type.
See ../README.md for the full interface + toolkit reference, and
../RULES.md before you start.
"""
from api import Verdict
import sys


def register(ctx):
    ctx.on("data_batch", check_data_batch)
    ctx.on("contract_checkpoint", check_contract_checkpoint)
    ctx.on("lineage_run", check_lineage_run)
    ctx.on("feature_materialization", check_feature_materialization)
    ctx.on("embedding_batch", check_embedding_batch)


# ---------------------------------------------------------------------------
# Helper: baseline comparison
# ---------------------------------------------------------------------------

def _exceeds(value, lo, hi, direction="outside"):
    """Return True if *value* is outside [lo, hi] in the given direction."""
    if direction == "outside":
        return value < lo or value > hi
    if direction == "above":
        return value > hi
    if direction == "below":
        return value < lo
    return False


def _bl(ctx, key, default=None):
    """Safe read of ctx.baseline; returns default when key is absent."""
    return ctx.baseline.get(key, default)


def _d(msg):
    """Debug print to stderr."""
    print(f"DBG:{msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 1. data_batch  —  checks pillar
# ---------------------------------------------------------------------------

def check_data_batch(payload, ctx):
    batch_id = payload["batch_id"]

    # --- memoization cache (keyed by batch_id) ---
    cache = ctx.state.setdefault("_db_cache", {})
    if batch_id in cache:
        return cache[batch_id]

    # Call the metered tool (cost: 1.0)
    profile = ctx.tools.batch_profile(batch_id)
    _d(f"DPROFILE:{batch_id}:{profile}")

    if "error" in profile:
        verdict = Verdict(alert=False, pillar="checks")
        cache[batch_id] = verdict
        return verdict

    row_count = profile["row_count"]
    null_rate = profile["null_rate"]["customer_id"]
    mean_amount = profile["mean_amount"]
    staleness_min = profile["staleness_min"]

    baseline = ctx.baseline
    alerts = []

    # --- null‑rate spike (baseline hard limit) ---
    null_rate_max = _bl(ctx, "null_rate_max", 0.01)
    if _exceeds(null_rate, 0.0, null_rate_max, "above"):
        alerts.append("null_spike")

    # --- staleness / freshness lag ---
    staleness_max = _bl(ctx, "staleness_min_max", 8.0)
    if _exceeds(staleness_min, 0.0, staleness_max, "above"):
        alerts.append("freshness_lag")

    # --- volume spike: row_count high *and* staleness also elevated ---
    row_max = _bl(ctx, "row_count_max", 500.0)
    if row_count > row_max * 1.3 and staleness_min > 3.0:
        alerts.append("volume_spike")
    elif row_count > row_max * 1.5:
        alerts.append("volume_spike")

    # --- distribution shift: mean_amount outside normal range ---
    amin = _bl(ctx, "mean_amount_min", 70.0)
    amax = _bl(ctx, "mean_amount_max", 95.0)
    if _exceeds(mean_amount, amin, amax, "outside"):
        alerts.append("distribution_shift")

    verdict = Verdict(alert=bool(alerts), pillar="checks") if alerts else Verdict(
        alert=False, pillar="checks"
    )
    cache[batch_id] = verdict
    return verdict


# ---------------------------------------------------------------------------
# 2. contract_checkpoint  —  contracts pillar
# ---------------------------------------------------------------------------

def check_contract_checkpoint(payload, ctx):
    contract_id = payload["contract_id"]
    checkpoint_batch_id = payload["checkpoint_batch_id"]

    # --- memoization cache ---
    cache = ctx.state.setdefault("_cc_cache", {})
    key = (contract_id, checkpoint_batch_id)
    if key in cache:
        return cache[key]

    # Call the metered tool (cost: 1.5)
    diff = ctx.tools.contract_diff(contract_id, checkpoint_batch_id)
    _d(f"DCONTRACT:{contract_id}:{checkpoint_batch_id}:{diff}")

    if "error" in diff:
        verdict = Verdict(alert=False, pillar="contracts")
        cache[key] = verdict
        return verdict

    violations = diff.get("violations", [])
    freshness_delay = diff.get("freshness_delay_min", float("inf"))

    baseline = ctx.baseline
    declared_sla = payload.get("declared_sla", {})
    declared_freshness = declared_sla.get("freshness_min", float("inf"))

    alerts = []

    # --- schema/hash violation ---
    if "schema_hash_mismatch" in violations:
        alerts.append("schema_break")

    # --- type violation (amount type not float) ---
    if "type_violation" in violations:
        alerts.append("type_violation")

    # --- freshness SLA violation ---
    if freshness_delay > declared_freshness:
        alerts.append("freshness_sla")

    # --- additional: freshness beyond the calibrated baseline ---
    fmax = _bl(ctx, "freshness_delay_max_min", 15.0)
    if freshness_delay > fmax:
        alerts.append("freshness_lag")

    verdict = Verdict(alert=bool(alerts), pillar="contracts") if alerts else Verdict(
        alert=False, pillar="contracts"
    )
    cache[key] = verdict
    return verdict


# ---------------------------------------------------------------------------
# 3. lineage_run  —  lineage pillar
# ---------------------------------------------------------------------------

def check_lineage_run(payload, ctx):
    run_id = payload["run_id"]

    # --- memoization cache ---
    cache = ctx.state.setdefault("_lr_cache", {})
    if run_id in cache:
        return cache[run_id]

    # Call the metered tool (cost: 1.0)
    slice_ = ctx.tools.lineage_graph_slice(run_id)
    _d(f"DLINEAGE:{run_id}:{slice_}")

    if "error" in slice_:
        verdict = Verdict(alert=False, pillar="lineage")
        cache[run_id] = verdict
        return verdict

    duration_ms = slice_.get("duration_ms", 0)
    actual_upstream = slice_.get("actual_upstream", [])
    actual_downstream_count = slice_.get("actual_downstream_count", 1)

    baseline = ctx.baseline
    alerts = []

    # --- orphaned output (no downstream consumers) ---
    if actual_downstream_count == 0:
        alerts.append("orphan_output")

    # --- missing upstream edges ---
    expected_upstream = {"raw.orders", "raw.customers"}
    actual_set = set(actual_upstream)
    if actual_set != expected_upstream:
        alerts.append("missing_upstream")

    # --- runtime anomaly (duration beyond calibrated maximum) ---
    dur_max = _bl(ctx, "lineage_duration_ms_max", 5000.0)
    if duration_ms > dur_max:
        alerts.append("runtime_anomaly")

    verdict = Verdict(alert=bool(alerts), pillar="lineage") if alerts else Verdict(
        alert=False, pillar="lineage"
    )
    cache[run_id] = verdict
    return verdict


# ---------------------------------------------------------------------------
# 4. feature_materialization  —  ai_infra pillar
# ---------------------------------------------------------------------------

def check_feature_materialization(payload, ctx):
    feature_view = payload["feature_view"]
    batch_id = payload["batch_id"]

    # --- memoization cache ---
    cache = ctx.state.setdefault("_fm_cache", {})
    cmap_key = (feature_view, batch_id)
    if cmap_key in cache:
        return cache[cmap_key]

    # Call the metered tool (cost: 2.0)
    drift = ctx.tools.feature_drift(feature_view, batch_id)
    _d(f"DFEATURE:{feature_view}:{drift}")

    if "error" in drift:
        verdict = Verdict(alert=False, pillar="ai_infra")
        cache[cmap_key] = verdict
        return verdict

    mean_shift_sigma = drift.get("mean_shift_sigma", 0.0)
    serve_mean = drift.get("serve_mean", 0.0)
    train_mean = drift.get("train_mean", 0.0)
    train_std = drift.get("train_std", 1.0)

    baseline = ctx.baseline
    alerts = []

    # --- feature skew: mean_shift_sigma exceeds calibrated maximum ---
    fmax = _bl(ctx, "feature_mean_shift_sigma_max", 0.4)
    if mean_shift_sigma > fmax:
        alerts.append("feature_skew")
    elif mean_shift_sigma > 0.25 and abs(serve_mean - train_mean) > 2 * train_std:
        # Subtle: shift below the hard max but still large in practice.
        alerts.append("feature_skew")

    # --- extra guard: extremely large serve‑train gap ---
    if abs(serve_mean - train_mean) > 3 * train_std:
        alerts.append("feature_skew")

    verdict = Verdict(alert=bool(alerts), pillar="ai_infra") if alerts else Verdict(
        alert=False, pillar="ai_infra"
    )
    cache[cmap_key] = verdict
    return verdict


# ---------------------------------------------------------------------------
# 5. embedding_batch  —  ai_infra pillar
# ---------------------------------------------------------------------------

def check_embedding_batch(payload, ctx):
    corpus = payload["corpus"]
    chunk_batch_id = payload["chunk_batch_id"]

    # --- memoization cache ---
    cache = ctx.state.setdefault("_eb_cache", {})
    if chunk_batch_id in cache:
        return cache[chunk_batch_id]

    # Call the metered tool (cost: 2.0)
    drift = ctx.tools.embedding_drift(corpus, chunk_batch_id)
    _d(f"DEMBED:{corpus}:{drift}")

    if "error" in drift:
        verdict = Verdict(alert=False, pillar="ai_infra")
        cache[chunk_batch_id] = verdict
        return verdict

    centroid_shift = drift.get("centroid_shift", 0.0)
    avg_doc_age_days = drift.get("avg_doc_age_days", 0.0)

    baseline = ctx.baseline
    alerts = []

    # --- embedding drift: centroid_shift beyond calibrated maximum ---
    cmax = _bl(ctx, "embedding_centroid_shift_max", 0.05)
    if centroid_shift > cmax:
        alerts.append("embedding_drift")
    # Subtle drift: shift below the max but still material.
    elif centroid_shift > 0.02 and avg_doc_age_days > 10:
        alerts.append("embedding_drift")

    # --- corpus staleness: docs too old ---
    cap_days = _bl(ctx, "corpus_avg_doc_age_days_max", 50.0)
    if avg_doc_age_days > cap_days:
        alerts.append("corpus_staleness")

    verdict = Verdict(alert=bool(alerts), pillar="ai_infra") if alerts else Verdict(
        alert=False, pillar="ai_infra"
    )
    cache[chunk_batch_id] = verdict
    return verdict
