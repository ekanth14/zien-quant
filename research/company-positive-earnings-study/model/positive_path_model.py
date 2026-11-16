"""Trainable, read-only positive-earnings conditional path model.

This is an empirical estimator, not a proven market law. NumPy is the only
third-party dependency. No broker requests, orders, model downloads or pickle.
Public-information reconstruction and operational app replay have distinct
availability rules: acquisition today does not invalidate an original public
financial vintage that was demonstrably available before a historical decision.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

IST = timezone(timedelta(hours=5, minutes=30))
VERSION = "positive-path-kernel-1.0"
MODES = {"PUBLIC_INFORMATION_BACKTEST", "OPERATIONAL_APP_REPLAY"}
LANDMARKS = ("09:30", "09:40", "10:00", "10:30")
REQUIRED_TARGETS = ("return_1100_bps", "max_up_bps", "max_down_bps")
INTEGRITY_FIELDS=('model','calibration','configuration','research_mode','synthetic_mechanics_only',
    'test_metrics','created_at','built_at','latest_information_used_at','landmark_ist','model_version',
    'dataset_sha256','feature_variant','feature_contract','predictions_enabled')
QUALITY_FLAGS = ("original_financial_vintage_verified", "source_timestamps_verified",
                 "price_adjustments_verified", "instrument_mapping_verified",
                 "bars_complete", "next_session_verified", "continuous_0915_1100",
                 "feature_definitions_verified")


@dataclass(frozen=True)
class Config:
    min_dates: int = 80
    min_rows: int = 300
    min_train_dates: int = 30
    min_selection_dates: int = 8
    min_calibration_dates: int = 12
    min_test_dates: int = 12
    min_symbols: int = 10
    min_class_train: int = 5
    min_local_event_ess: float = 20.
    min_local_date_ess: float = 8.
    train_fraction: float = .60
    calibration_fraction: float = .20
    coverage: float = .80
    bandwidths: tuple[float, ...] = (.5, 1., 2.)
    half_lives_days: tuple[float, ...] = (180., 730.)
    sector_shrinkages: tuple[float, ...] = (10., 30.)
    ridge_penalties: tuple[float, ...] = (.001,.01,.1,1.,10.)
    temperatures: tuple[float, ...] = (.5, .75, 1., 1.25, 1.5, 2., 3.)
    ood_z_limit: float = 8.
    bootstrap_repetitions: int = 500
    seed: int = 7129


def timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Timezone-aware ISO timestamp required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timezone-aware ISO timestamp required")
    return result.astimezone(IST)


def finite(value: Any) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def canonical_hash(value: Any) -> str:
    def sanitized(v):
        if isinstance(v,float) and not math.isfinite(v):return {"invalid_nonfinite":str(v)}
        if isinstance(v,dict):return {str(k):sanitized(x) for k,x in v.items()}
        if isinstance(v,(list,tuple)):return [sanitized(x) for x in v]
        return v
    return hashlib.sha256(json.dumps(sanitized(value), sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def artifact_integrity(artifact:dict)->str:
    return canonical_hash({k:artifact.get(k) for k in INTEGRITY_FIELDS})


def row_reasons(row: dict, mode: str, *, training: bool = True) -> list[str]:
    """Validate scope, information clock, complete targets and input lineage.

    `features_known_at` must already include the latest actually used dependency.
    Specific public financial/bar clocks are checked again, not merely trusted.
    `acquired_at` is provenance in public-reconstruction mode, never a pretend
    historical publication time. In operational mode actual app clocks matter.
    """
    reasons = []
    try:
        decision = timestamp(row["decision_at"])
        day = date.fromisoformat(row["session_date"])
        announcement = timestamp(row["announced_at"])
        if mode not in MODES:
            reasons.append("UNKNOWN_RESEARCH_MODE")
        if announcement.date() < date(2023, 5, 6) or announcement.date() >= day:
            reasons.append("ANNOUNCEMENT_DATE_OR_LITERAL_NEXT_DATE_OUT_OF_SCOPE")
        if decision.date() != day or decision.strftime("%H:%M") not in LANDMARKS or decision.second:
            reasons.append("INVALID_DECISION_LANDMARK")
        if timestamp(row["features_known_at"]) > decision:
            reasons.append("FEATURES_NOT_AVAILABLE_AT_DECISION")
        provenance = row.get("provenance", {})
        financial_version = timestamp(provenance["financial_version_available_at"])
        original_publication = timestamp(provenance["original_publication_at"])
        if max(financial_version, original_publication) > decision:
            reasons.append("PUBLIC_FINANCIAL_VERSION_AFTER_DECISION")
        last_end = timestamp(provenance["last_feature_bar_end"])
        lag = provenance.get("registered_bar_lag_seconds")
        if not finite(lag) or lag < 0 or lag > 3600:
            reasons.append("MISSING_OR_INVALID_REGISTERED_BAR_LAG")
        elif last_end + timedelta(seconds=lag) > decision:
            reasons.append("UNAVAILABLE_OR_UNCLOSED_FEATURE_BAR")
        else:
            opening=datetime.combine(day,time(9,15),IST)
            expected=max(0,min(21,int((decision-timedelta(seconds=lag)-opening).total_seconds()//300)))
            if expected<3:reasons.append('OPENING_RANGE_NOT_AVAILABLE_UNDER_REGISTERED_LAG')
            if last_end!=opening+timedelta(minutes=5*expected):
                reasons.append('STALE_OR_MISALIGNED_FEATURE_SNAPSHOT')
        if last_end.date() != day or last_end.time() < time(9, 15):
            reasons.append("INVALID_FEATURE_BAR_WINDOW")
        dependencies = [financial_version, original_publication, last_end + timedelta(seconds=lag or 0)]
        if timestamp(row["features_known_at"]) < max(dependencies):
            reasons.append("FEATURE_CLOCK_PRECEDES_A_USED_DEPENDENCY")
        if mode == "OPERATIONAL_APP_REPLAY":
            app_seen = timestamp(provenance["app_first_seen_at"])
            extracted = timestamp(provenance["extraction_completed_at"])
            arrived = timestamp(provenance["last_feature_bar_arrived_at"])
            if arrived<last_end:reasons.append('BAR_ARRIVAL_PRECEDES_BAR_CLOSE')
            if max(app_seen, extracted, arrived) > decision:
                reasons.append("APP_INPUTS_NOT_AVAILABLE_AT_DECISION")
        eligibility = row.get("eligibility", {})
        if any(eligibility.get(k) is not True for k in ("positive", "leverage_above_3", "in_dashboard")):
            reasons.append("NOT_ELIGIBLE_ORDINARY_POSITIVE_RESULT")
        quality = row.get("quality", {})
        reasons.extend("UNVERIFIED_" + flag.upper() for flag in QUALITY_FLAGS if quality.get(flag) is not True)
        if not isinstance(row.get("event_id"), str) or not row["event_id"] or not row.get("symbol"):
            reasons.append("MISSING_EVENT_IDENTITY")
        features = row.get("features", {})
        if not isinstance(row.get('feature_variant'),str) or not row['feature_variant']:
            reasons.append('MISSING_EXPLICIT_FEATURE_VARIANT')
        if not isinstance(features, dict) or not features or any(not finite(v) for v in features.values()):
            reasons.append("MISSING_OR_NONFINITE_FEATURES")
        if any(k.startswith('market_') or k.startswith('stock_minus_market_') for k in features):
            benchmark=provenance.get('benchmark',{})
            if not isinstance(benchmark,dict):
                reasons.append('INVALID_BENCHMARK_LINEAGE')
            else:
                if any(benchmark.get(k) is not True for k in ('provider_verified','source_timestamps_verified','price_adjustments_verified','instrument_mapping_verified')):
                    reasons.append('UNVERIFIED_BENCHMARK_SOURCE_OR_DEFINITIONS')
                if not isinstance(benchmark.get('instrument'),str) or not benchmark['instrument']:
                    reasons.append('MISSING_BENCHMARK_INSTRUMENT')
                benchmark_end=timestamp(benchmark['last_feature_bar_end'])
                benchmark_lag=benchmark.get('registered_bar_lag_seconds')
                if benchmark.get('bar_interval_minutes')!=5 or not finite(benchmark_lag) or benchmark_lag!=lag:
                    reasons.append('BENCHMARK_INTERVAL_OR_LAG_MISMATCH')
                elif benchmark_end.date()!=day or benchmark_end!=last_end or benchmark_end+timedelta(seconds=benchmark_lag)>decision:
                    reasons.append('STALE_UNALIGNED_OR_UNAVAILABLE_BENCHMARK_SNAPSHOT')
        # The movement band is pre-registered and knowable at decision, not
        # derived from the realized post-decision range or model's prediction.
        if not finite(row.get("movement_band_bps")) or row["movement_band_bps"] <= 0:
            reasons.append("INVALID_REGISTERED_MOVEMENT_BAND")
        if not finite(row.get("reference_price")) or row["reference_price"] <= 0:
            reasons.append("INVALID_AVAILABLE_REFERENCE_PRICE")
        if training:
            target_at = timestamp(row["target_available_at"])
            finish = datetime.combine(day, time(11), IST)
            if target_at < finish or target_at <= decision:
                reasons.append("TARGET_CLOCK_BEFORE_OUTCOME")
            targets = row.get("targets", {})
            if not all(k in targets and finite(targets[k]) for k in REQUIRED_TARGETS):
                reasons.append("MISSING_REQUIRED_TARGETS")
            elif targets["max_up_bps"] < 0 or targets["max_down_bps"] > 0:
                reasons.append("INVALID_FUTURE_EXCURSION_SIGNS")
            elif not targets["max_down_bps"] <= targets["return_1100_bps"] <= targets["max_up_bps"]:
                reasons.append("RETURN_OUTSIDE_FUTURE_EXTREMES")
            if any(not finite(v) for v in targets.values()):
                reasons.append("NONFINITE_TARGET")
            for key, cutoff in (("return_1000_bps", time(10)), ("return_1030_bps", time(10, 30))):
                if key in targets and decision.time() >= cutoff:
                    reasons.append("PAST_OR_CURRENT_CHECKPOINT_IS_NOT_A_FUTURE_TARGET")
    except (KeyError, TypeError, ValueError, OverflowError):
        reasons.append("INVALID_SCOPE_OR_LINEAGE_SCHEMA")
    return list(dict.fromkeys(reasons))


def validate_dataset(dataset: dict, landmark: str) -> tuple[list[dict], dict]:
    mode = dataset.get("research_mode")
    valid, rejected, excluded, considered = [], [], 0, []
    seen = set()
    for i, row in enumerate(dataset.get("rows", [])):
        if not isinstance(row, dict):
            rejected.append({"row": i, "reasons": ["INVALID_ROW"]}); continue
        try:
            if timestamp(row.get("decision_at")).strftime("%H:%M") != landmark:
                excluded += 1; continue
        except (ValueError, TypeError, OverflowError):
            pass
        considered.append(row)
        reasons = row_reasons(row, mode)
        if dataset.get('feature_variant') and row.get('feature_variant')!=dataset['feature_variant']:
            reasons.append('ROW_FEATURE_VARIANT_DIFFERS_FROM_DATASET_CONTRACT')
        key = (row.get("symbol"), row.get("session_date"), landmark)
        if key in seen:
            reasons.append("DUPLICATE_COMPANY_SESSION_LANDMARK")
        seen.add(key)
        if reasons:
            rejected.append({"row": i, "event_id": row.get("event_id"), "symbol": row.get("symbol"), "reasons": reasons})
        else:
            valid.append(row)
    if valid:
        feature_names = sorted(valid[0]["features"])
        targets = sorted(valid[0]["targets"])
        remaining = []
        first=valid[0]
        first_contract=(first['provenance']['registered_bar_lag_seconds'],first['movement_band_bps'],
            first.get('feature_variant',dataset.get('feature_variant','UNSPECIFIED_EXPLICIT_FEATURE_KEYS')),
            first['provenance'].get('benchmark',{}).get('instrument'))
        for row in valid:
            if sorted(row["features"]) != feature_names or sorted(row["targets"]) != targets:
                rejected.append({"event_id": row["event_id"], "reasons": ["FEATURE_OR_TARGET_VARIANT_MISMATCH"]})
            elif (row['provenance']['registered_bar_lag_seconds'],row['movement_band_bps'],
                    row.get('feature_variant',dataset.get('feature_variant','UNSPECIFIED_EXPLICIT_FEATURE_KEYS')),
                    row['provenance'].get('benchmark',{}).get('instrument'))!=first_contract:
                rejected.append({'event_id':row['event_id'],'reasons':['FEATURE_LAG_BAND_OR_BENCHMARK_CONTRACT_MISMATCH']})
            else:
                remaining.append(row)
        valid = remaining
    audit = {"input_rows": len(dataset.get("rows", [])), "eligible_verified_rows": len(valid),
             "raw_landmark_rows":len(considered),
             "raw_landmark_distinct_dates":len({r.get("session_date") for r in considered if r.get("session_date")}),
             "raw_landmark_distinct_symbols":len({r.get("symbol") for r in considered if r.get("symbol")}),
             "eligible_distinct_dates": len({r["session_date"] for r in valid}),
             "eligible_distinct_symbols": len({r["symbol"] for r in valid}),
             "other_landmark_rows": excluded, "rejected_rows": len(rejected), "rejections": rejected,
             "rejection_counts": dict(collections.Counter(reason for r in rejected for reason in r["reasons"]))}
    return sorted(valid, key=lambda r: (r["session_date"], r["symbol"])), audit


def date_weights(rows: list[dict]) -> np.ndarray:
    counts = collections.Counter(r["session_date"] for r in rows)
    weights = np.asarray([1. / counts[r["session_date"]] for r in rows])
    return weights / weights.sum()


def effective_size(weights: np.ndarray) -> float:
    total = float(np.sum(weights))
    return total * total / float(np.dot(weights, weights)) if total > 0 else 0.


def weighted_quantile(values: np.ndarray, weights: np.ndarray, probabilities, *, order=None) -> np.ndarray:
    # Inverse empirical CDF; no interpolation of market candles or paths.
    order = np.argsort(values, kind="stable") if order is None else order
    cumulative = np.cumsum(weights[order])
    cumulative /= cumulative[-1]
    return values[order[np.minimum(np.searchsorted(cumulative, probabilities, side="left"), len(order)-1)]]


def class_index(value: float, band: float) -> int:
    return 0 if value < -band else 2 if value > band else 1


def fit_kernel(rows: list[dict], hyper: dict) -> dict:
    names = sorted(rows[0]["features"])
    target_names = sorted(rows[0]["targets"])
    x = np.asarray([[r["features"][k] for k in names] for r in rows], dtype=float)
    weights = date_weights(rows)
    median = np.asarray([weighted_quantile(x[:,j],weights,[.5])[0] for j in range(x.shape[1])])
    mad = np.asarray([weighted_quantile(np.abs(x[:,j]-median[j]),weights,[.5])[0] for j in range(x.shape[1])])*1.4826
    # Constant inputs have scale one; they still trigger OOD checks if a query
    # moves far outside the training range. No statistics are fitted on test.
    mean=np.sum(weights[:,None]*x,axis=0)
    weighted_std=np.sqrt(np.sum(weights[:,None]*(x-mean)**2,axis=0))
    scale = np.where(mad > 1e-8, mad, np.maximum(weighted_std, 1.))
    y = np.asarray([[r["targets"][k] for k in target_names] for r in rows], dtype=float)
    z = (x-median)/scale
    design = np.column_stack((np.ones(len(z)), z))
    penalty = np.eye(design.shape[1]) * hyper.get('ridge_lambda',1.)
    penalty[0, 0] = 0.
    ridge = np.linalg.solve(design.T @ (weights[:, None] * design) + penalty,
                            design.T @ (weights[:, None] * y))
    counts = collections.Counter(r["session_date"] for r in rows)
    return {"feature_names": names, "target_names": target_names,
            "median": median.tolist(), "scale": scale.tolist(), "x_standardized": z.tolist(), "y": y.tolist(),
            "dates": [r["session_date"] for r in rows], "symbols": [r["symbol"] for r in rows],
            "event_ids": [r["event_id"] for r in rows], "sectors": [r.get("sector", "UNKNOWN") for r in rows],
            "date_count_per_row": [counts[r["session_date"]] for r in rows],
            "target_available_at": [r["target_available_at"] for r in rows],
            "hyperparameters": hyper, "ridge_coefficients": ridge.tolist(),
            "baseline_weights": weights.tolist()}


def prepare_model(model:dict)->dict:
    """Build reusable numerical arrays without mutating the JSON artifact."""
    _,date_inverse=np.unique(model['dates'],return_inverse=True)
    return {'x':np.asarray(model['x_standardized']),'y':np.asarray(model['y']),
        'date_ordinals':np.asarray([date.fromisoformat(d).toordinal() for d in model['dates']]),
        'target_unix':np.asarray([timestamp(t).timestamp() for t in model['target_available_at']]),
        'event_ids':np.asarray(model['event_ids']),'sectors':np.asarray(model['sectors']),
        'date_count':np.asarray(model['date_count_per_row']),'date_inverse':date_inverse,
        'baseline_weights':np.asarray(model['baseline_weights']),
        'ridge_coefficients':np.asarray(model['ridge_coefficients']),
        'target_orders':[np.argsort(np.asarray(model['y'])[:,j],kind='stable') for j in range(len(model['target_names']))]}


def raw_prediction(model: dict, row: dict, temperature: float = 1., *, prepared:dict|None=None) -> dict:
    prepared=prepared or prepare_model(model)
    names = model["feature_names"]
    z = (np.asarray([row["features"][k] for k in names])-model["median"]) / model["scale"]
    x = prepared['x']
    y = prepared['y']
    hyper = model["hyperparameters"]
    query_date = date.fromisoformat(row["session_date"])
    ages = query_date.toordinal()-prepared['date_ordinals']
    decision = timestamp(row["decision_at"])
    # Scales, baselines and hyperparameters already depend on all fitting rows.
    # Masking a future row's weight cannot undo that information. Reject the
    # complete artifact if any fitting information is not strictly earlier.
    if np.any(prepared['target_unix']>=decision.timestamp()) or np.any(ages<=0):
        raise ValueError('MODEL_INCLUDES_FUTURE_OR_SAME_SESSION_INFORMATION')
    if np.any(prepared['event_ids']==row['event_id']):
        raise ValueError('MODEL_CONTAINS_QUERY_EVENT')
    allowed = (prepared['target_unix']<decision.timestamp()) & (prepared['event_ids']!=row['event_id']) & (ages>0)
    distance = np.mean((x-z)**2, axis=1)
    logweights = -distance/(2*hyper["bandwidth"]**2) - np.maximum(ages,0)*math.log(2)/hyper["half_life_days"] - np.log(prepared['date_count'])
    logweights = np.where(allowed, logweights, -np.inf)
    if not np.any(np.isfinite(logweights)):
        raise ValueError("NO_PRIOR_AVAILABLE_TRAINING_LABELS")
    weights = np.exp(logweights-np.max(logweights))
    weights /= weights.sum()
    sector = row.get("sector", "UNKNOWN")
    same_sector = (prepared['sectors']==sector) & (sector!='UNKNOWN')
    sw = weights*same_sector
    if sw.sum() > 0:
        sector_ess = effective_size(sw)
        rho = sector_ess/(sector_ess+hyper["sector_shrinkage"])
        weights = (1-rho)*weights + rho*sw/sw.sum()
    ess = effective_size(weights)
    date_ess = effective_size(np.bincount(prepared['date_inverse'],weights=weights))
    return_index = model["target_names"].index("return_1100_bps")
    labels=np.where(y[:,return_index]<-row['movement_band_bps'],0,np.where(y[:,return_index]>row['movement_band_bps'],2,1))
    frequencies = np.asarray([weights[labels == k].sum() for k in range(3)])
    baseline = prepared['baseline_weights']
    prior = np.asarray([baseline[labels == k].sum() for k in range(3)])
    # Weak pooled pseudocounts prevent exact 0/1 confidence. Temperature is
    # selected solely on the later calibration block.
    p = (ess*frequencies+3*prior)/(ess+3)
    logits = np.log(np.maximum(p,1e-12))/temperature
    p = np.exp(logits-np.max(logits)); p /= p.sum()
    quantiles = {name: weighted_quantile(y[:,j],weights,[.1,.5,.9],order=prepared['target_orders'][j]).tolist()
                 for j,name in enumerate(model["target_names"])}
    return {"probabilities": p.tolist(), "mean": dict(zip(model["target_names"],(weights@y).tolist())),
            "quantiles": quantiles, "event_effective_sample_size": ess, "date_effective_sample_size": date_ess,
            "max_abs_standardized_feature": float(np.max(np.abs(z))),
            "ridge_mean": dict(zip(model["target_names"],(np.r_[1.,z]@prepared['ridge_coefficients']).tolist()))}


def cross_entropy(probabilities, labels, weights) -> float:
    return float(-np.sum(weights*np.log(np.maximum(np.asarray(probabilities)[np.arange(len(labels)),labels],1e-12))))


def brier(probabilities, labels, weights) -> float:
    actual = np.eye(3)[labels]
    return float(np.sum(weights*np.sum((np.asarray(probabilities)-actual)**2,axis=1)))


def chronological_split(rows: list[dict], config: Config) -> tuple[list, list, list]:
    dates = sorted({r["session_date"] for r in rows})
    nfit = int(len(dates)*config.train_fraction)
    ncal = int(len(dates)*config.calibration_fraction)
    if not 0 < nfit < nfit+ncal < len(dates):
        raise ValueError("INVALID_CHRONOLOGICAL_PARTITIONS")
    fitdates, caldates, testdates = set(dates[:nfit]), set(dates[nfit:nfit+ncal]), set(dates[nfit+ncal:])
    return ([r for r in rows if r["session_date"] in fitdates],
            [r for r in rows if r["session_date"] in caldates],
            [r for r in rows if r["session_date"] in testdates])


def partition_summary(rows: list[dict]) -> dict:
    dates = sorted({r["session_date"] for r in rows})
    return {"rows": len(rows), "dates": len(dates), "start_date": dates[0] if dates else None,
            "end_date": dates[-1] if dates else None, "symbols": len({r["symbol"] for r in rows})}


def purge_not_known_before(rows: list[dict], boundary: datetime) -> list[dict]:
    return [r for r in rows if timestamp(r["target_available_at"]) < boundary]


def select_hyperparameters(rows: list[dict], config: Config) -> tuple[dict, list[dict]]:
    dates = sorted({r["session_date"] for r in rows})
    count = max(config.min_selection_dates, int(len(dates)*.2))
    selection_dates = set(dates[-count:])
    selection = [r for r in rows if r["session_date"] in selection_dates]
    cutoff = min(timestamp(r["decision_at"]) for r in selection)
    fit = purge_not_known_before([r for r in rows if r["session_date"] not in selection_dates],cutoff)
    if len({r["session_date"] for r in fit}) < config.min_train_dates:
        raise ValueError("INSUFFICIENT_INNER_FIT_DATES")
    y = np.asarray([r["targets"]["return_1100_bps"] for r in selection])
    scale = max(float(np.std([r["targets"]["return_1100_bps"] for r in fit])),10.)
    labels = np.asarray([class_index(v,r["movement_band_bps"]) for v,r in zip(y,selection)])
    weights = date_weights(selection)
    trials = []
    for bandwidth in config.bandwidths:
        for half_life in config.half_lives_days:
            for shrinkage in config.sector_shrinkages:
                hyper={"bandwidth":bandwidth,"half_life_days":half_life,"sector_shrinkage":shrinkage}
                model=fit_kernel(fit,hyper)
                prepared=prepare_model(model)
                pred=[raw_prediction(model,r,prepared=prepared) for r in selection]
                loss=cross_entropy([p["probabilities"] for p in pred],labels,weights)
                mae=float(np.sum(weights*np.abs(y-np.asarray([p["mean"]["return_1100_bps"] for p in pred]))))
                trials.append({**hyper,"selection_log_loss":loss,"selection_mae_bps":mae,
                               "registered_objective":loss+.1*mae/scale})
    winner=min(trials,key=lambda t:t["registered_objective"])
    chosen={k:winner[k] for k in ("bandwidth","half_life_days","sector_shrinkage")}
    ridge_trials=[]
    target_names=sorted(fit[0]['targets'])
    target_scale=np.maximum(np.std([[r['targets'][k] for k in target_names] for r in fit],axis=0),10.)
    observed=np.asarray([[r['targets'][k] for k in target_names] for r in selection])
    for lam in config.ridge_penalties:
        ridge_model=fit_kernel(fit,{**chosen,'ridge_lambda':lam})
        selection_x=np.asarray([[r['features'][k] for k in ridge_model['feature_names']] for r in selection])
        selection_z=(selection_x-ridge_model['median'])/ridge_model['scale']
        predicted=np.column_stack((np.ones(len(selection_z)),selection_z))@np.asarray(ridge_model['ridge_coefficients'])
        objective=float(np.sum(weights*np.mean(np.abs(observed-predicted)/target_scale,axis=1)))
        ridge_trials.append({'model':'RIDGE_BASELINE','ridge_lambda':lam,'selection_normalized_mae':objective})
    chosen['ridge_lambda']=min(ridge_trials,key=lambda t:t['selection_normalized_mae'])['ridge_lambda']
    return chosen,trials+ridge_trials


def calibrate(model: dict, rows: list[dict], config: Config) -> dict:
    prepared=prepare_model(model)
    predictions=[raw_prediction(model,r,prepared=prepared) for r in rows]
    labels=np.asarray([class_index(r["targets"]["return_1100_bps"],r["movement_band_bps"]) for r in rows])
    weights=date_weights(rows)
    trials=[]
    for temperature in config.temperatures:
        ps=[]
        for pred in predictions:
            logits=np.log(np.maximum(pred["probabilities"],1e-12))/temperature
            p=np.exp(logits-np.max(logits));ps.append(p/p.sum())
        trials.append({"temperature":temperature,"calibration_log_loss":cross_entropy(ps,labels,weights)})
    best=min(trials,key=lambda r:r["calibration_log_loss"])
    y=np.asarray(model["y"])
    scales=np.maximum(np.std(y,axis=0),10.)
    day_scores=collections.defaultdict(list)
    for row,pred in zip(rows,predictions):
        scores=[]
        for j,name in enumerate(model["target_names"]):
            lo,_,hi=pred["quantiles"][name]
            actual=row["targets"][name]
            scores.append(max(lo-actual,actual-hi,0.)/scales[j])
        day_scores[row["session_date"]].append(max(scores))
    # One maximum nonconformity per date gives an honest cluster unit. Its
    # nominal coverage interpretation requires exchangeable date blocks, which
    # is not guaranteed for financial time series; actual test coverage is shown.
    values=sorted(max(v) for v in day_scores.values())
    order=math.ceil((len(values)+1)*config.coverage)
    if order>len(values):
        raise ValueError("INSUFFICIENT_CONFORMAL_DATE_BLOCKS")
    adjustment=values[order-1]
    return {"temperature":best["temperature"],"temperature_trials":trials,
            "conformal_cluster":"session_date_max_over_issuers_and_targets",
            "registered_coverage":config.coverage,"date_block_count":len(values),
            "target_scales_bps":dict(zip(model["target_names"],scales.tolist())),
            "normalized_conformal_adjustment":adjustment,
            "coverage_caveat":"Nominal date-block exchangeability is not guaranteed; inspect untouched-date empirical coverage."}


def apply_intervals(pred: dict, calibration: dict) -> dict:
    result={}
    for name,(lo,median,hi) in pred["quantiles"].items():
        pad=calibration["normalized_conformal_adjustment"]*calibration["target_scales_bps"][name]
        lo,hi=lo-pad,hi+pad
        if name=="max_up_bps":lo,hi=max(0.,lo),max(0.,hi)
        if name=="max_down_bps":lo,hi=min(0.,lo),min(0.,hi)
        result[name]={"lower_bps":lo,"median_bps":median,"upper_bps":hi}
    return result


def evaluate(model: dict, calibration: dict, fit_rows: list[dict], test_rows: list[dict], config: Config) -> dict:
    prepared=prepare_model(model)
    predictions=[raw_prediction(model,r,calibration["temperature"],prepared=prepared) for r in test_rows]
    weights=date_weights(test_rows)
    labels=np.asarray([class_index(r["targets"]["return_1100_bps"],r["movement_band_bps"]) for r in test_rows])
    train_weights=date_weights(fit_rows)
    baselines=[]
    train_y=np.asarray([r["targets"]["return_1100_bps"] for r in fit_rows])
    for row in test_rows:
        train_labels=np.asarray([class_index(v,row["movement_band_bps"]) for v in train_y])
        baselines.append(np.asarray([train_weights[train_labels==k].sum() for k in range(3)]))
    model_probs=np.asarray([p["probabilities"] for p in predictions])
    baseline_probs=np.asarray(baselines)
    actual=np.eye(3)[labels]
    model_row_loss=-np.log(np.maximum(model_probs[np.arange(len(labels)),labels],1e-12))
    baseline_row_loss=-np.log(np.maximum(baseline_probs[np.arange(len(labels)),labels],1e-12))
    gains=collections.defaultdict(list)
    for row,gain in zip(test_rows,baseline_row_loss-model_row_loss):gains[row["session_date"]].append(float(gain))
    date_gains=np.asarray([np.mean(v) for v in gains.values()])
    rng=np.random.default_rng(config.seed)
    block_length=max(1,int(math.sqrt(len(date_gains))))
    draws=[]
    for _ in range(config.bootstrap_repetitions):
        sampled=[]
        while len(sampled)<len(date_gains):
            start=int(rng.integers(0,len(date_gains)-block_length+1))
            sampled.extend(date_gains[start:start+block_length])
        draws.append(float(np.mean(sampled[:len(date_gains)])))
    draws=np.asarray(draws)
    interval_metrics={}
    intervals=[apply_intervals(p,calibration) for p in predictions]
    for name in model["target_names"]:
        targets=np.asarray([r["targets"][name] for r in test_rows])
        means=np.asarray([p["mean"][name] for p in predictions])
        ridge=np.asarray([p["ridge_mean"][name] for p in predictions])
        baseline_mean=float(np.sum(train_weights*np.asarray([r["targets"][name] for r in fit_rows])))
        coverage=np.asarray([v[name]["lower_bps"]<=value<=v[name]["upper_bps"] for v,value in zip(intervals,targets)])
        widths=np.asarray([v[name]["upper_bps"]-v[name]["lower_bps"] for v in intervals])
        interval_metrics[name]={"mae_bps":float(np.sum(weights*np.abs(targets-means))),
            "ridge_mae_bps":float(np.sum(weights*np.abs(targets-ridge))),
            "classless_mean_baseline_mae_bps":float(np.sum(weights*np.abs(targets-baseline_mean))),
            "empirical_interval_coverage":float(np.sum(weights*coverage)),
            "mean_interval_width_bps":float(np.sum(weights*widths))}
    joint_row_covered=[]
    joint_dates=collections.defaultdict(list)
    for row,interval in zip(test_rows,intervals):
        covered=all(interval[k]['lower_bps']<=row['targets'][k]<=interval[k]['upper_bps'] for k in model['target_names'])
        joint_row_covered.append(covered);joint_dates[row['session_date']].append(covered)
    ll=cross_entropy(model_probs,labels,weights);base_ll=cross_entropy(baseline_probs,labels,weights)
    bs=brier(model_probs,labels,weights);base_bs=brier(baseline_probs,labels,weights)
    accuracy=float(np.sum(weights*(np.argmax(model_probs,axis=1)==labels)))
    base_accuracy=float(np.sum(weights*(np.argmax(baseline_probs,axis=1)==labels)))
    reasons=[]
    if not ll<base_ll:reasons.append("NO_LOG_LOSS_IMPROVEMENT_OVER_PRIOR")
    if not bs<base_bs:reasons.append("NO_BRIER_IMPROVEMENT_OVER_PRIOR")
    ci=np.quantile(draws,[.025,.975]).tolist()
    if ci[0]<=0:reasons.append("DATE_BOOTSTRAP_LOG_LOSS_IMPROVEMENT_UNCERTAIN")
    component_gates={}
    for name in model['target_names']:
        metric=interval_metrics[name]
        improved=metric['mae_bps']<min(metric['ridge_mae_bps'],metric['classless_mean_baseline_mae_bps'])
        component_gates[name]={'mae_improves_best_simple_baseline':improved}
        if not improved:reasons.append('NO_'+name.upper()+'_MAE_IMPROVEMENT_OVER_SIMPLE_BASELINES')
    if any(m["empirical_interval_coverage"]<config.coverage-.10 for m in interval_metrics.values()):
        reasons.append("HELDOUT_INTERVAL_UNDERCOVERAGE")
    simultaneous_date_coverage=float(np.mean([all(values) for values in joint_dates.values()]))
    if simultaneous_date_coverage<config.coverage-.10:
        reasons.append('HELDOUT_SIMULTANEOUS_DATE_INTERVAL_UNDERCOVERAGE')
    return {"log_loss":ll,"prior_baseline_log_loss":base_ll,"brier":bs,"prior_baseline_brier":base_bs,
            "direction_accuracy":accuracy,"prior_baseline_direction_accuracy":base_accuracy,
            "date_bootstrap_log_loss_gain_95_interval":ci,
            "bootstrap_method":"paired_moving_session_date_blocks",
            "bootstrap_block_length_dates":block_length,
            "joint_all_targets_row_coverage":float(np.sum(weights*np.asarray(joint_row_covered))),
            "simultaneous_all_issuers_all_targets_date_coverage":simultaneous_date_coverage,
            "joint_coverage_date_count":len(joint_dates),
            "joint_coverage_caveat":"Whole-date simultaneous coverage is separately reported; a small or dependent test-date sample does not establish future conditional coverage.",
            "magnitude_component_gates":component_gates,
            "magnitude_metrics":interval_metrics,"release_gate_passed":not reasons,"release_gate_reasons":reasons,
            "test_period":partition_summary(test_rows),"accuracy_is_market_evidence_only_if_input_vintages_verified":True}


def train(dataset: dict, landmark: str="09:40", config: Config | None=None, *, created_at: str | None=None) -> dict:
    config=config or Config()
    rows,audit=validate_dataset(dataset,landmark)
    created_at=created_at or datetime.now(IST).isoformat()
    training_time=timestamp(created_at)
    unavailable=[r for r in rows if timestamp(r["target_available_at"])>training_time]
    rows=[r for r in rows if timestamp(r["target_available_at"])<=training_time]
    if unavailable:
        audit["rejections"].extend({"event_id":r["event_id"],"reasons":["TARGET_NOT_AVAILABLE_AT_TRAINING_TIME"]} for r in unavailable)
        audit["rejected_rows"]+=len(unavailable)
        audit["rejection_counts"]["TARGET_NOT_AVAILABLE_AT_TRAINING_TIME"]=len(unavailable)
        audit.update(eligible_verified_rows=len(rows),eligible_distinct_dates=len({r["session_date"] for r in rows}),
                     eligible_distinct_symbols=len({r["symbol"] for r in rows}))
    artifact={"schema_version":"1.0","model_version":VERSION,"research_mode":dataset.get("research_mode"),
              "landmark_ist":landmark,"created_at":created_at,
              "built_at":datetime.now(IST).isoformat(),'latest_information_used_at':None,
              'feature_variant':dataset.get('feature_variant','UNSPECIFIED_EXPLICIT_FEATURE_KEYS'),
              'feature_contract':None,
              "dataset_sha256":canonical_hash(dataset),"configuration":asdict(config),"data_audit":audit,
              "status":"INSUFFICIENT_VERIFIED_DATA","predictions_enabled":False,"orders_enabled":False,
              "model":None,"calibration":None,"test_metrics":None,
              "synthetic_mechanics_only":dataset.get("synthetic_mechanics_only") is True,
              "prediction_meaning":"Raw reference-price moves and future high/low excursions; no fill, fees or trading profit assumed."}
    reasons=[]
    if len(rows)<config.min_rows:reasons.append("INSUFFICIENT_VERIFIED_ROWS")
    if audit["eligible_distinct_dates"]<config.min_dates:reasons.append("INSUFFICIENT_INDEPENDENT_DATE_COVERAGE")
    if audit["eligible_distinct_symbols"]<config.min_symbols:reasons.append("INSUFFICIENT_ISSUER_DIVERSITY")
    if reasons:
        artifact["blockers"]=reasons;return artifact
    artifact['feature_contract']={'feature_names':sorted(rows[0]['features']),
        'feature_variant':rows[0].get('feature_variant',artifact['feature_variant']),
        'registered_bar_lag_seconds':rows[0]['provenance']['registered_bar_lag_seconds'],
        'movement_band_bps':rows[0]['movement_band_bps'],
        'benchmark_instrument':rows[0]['provenance'].get('benchmark',{}).get('instrument')}
    artifact['feature_variant']=artifact['feature_contract']['feature_variant']
    fit,cal,test=chronological_split(rows,config)
    fit=purge_not_known_before(fit,min(timestamp(r["decision_at"]) for r in cal))
    cal=purge_not_known_before(cal,min(timestamp(r["decision_at"]) for r in test))
    summary={"fit":partition_summary(fit),"calibration":partition_summary(cal),"test":partition_summary(test)}
    artifact["partitions"]=summary
    artifact['latest_information_used_at']=max(timestamp(r['target_available_at']) for r in fit+cal+test).isoformat()
    for key,minimum in (("fit",config.min_train_dates+config.min_selection_dates),("calibration",config.min_calibration_dates),("test",config.min_test_dates)):
        if summary[key]["dates"]<minimum:reasons.append("INSUFFICIENT_"+key.upper()+"_DATES")
    labels=collections.Counter(class_index(r["targets"]["return_1100_bps"],r["movement_band_bps"]) for r in fit)
    if any(labels[k]<config.min_class_train for k in range(3)):reasons.append("INSUFFICIENT_DIRECTION_CLASS_SUPPORT")
    if reasons:artifact["blockers"]=reasons;return artifact
    try:
        hyper,trials=select_hyperparameters(fit,config)
        model=fit_kernel(fit,hyper)
        calibration=calibrate(model,cal,config)
        metrics=evaluate(model,calibration,fit,test,config)
    except (ValueError,np.linalg.LinAlgError,FloatingPointError) as exc:
        artifact["status"]="TRAINING_REJECTED";artifact["blockers"]=[str(exc)];return artifact
    artifact.update(model=model,calibration=calibration,test_metrics=metrics,selection_trials=trials,
                    status="VALIDATED_RESEARCH_FORECAST" if metrics["release_gate_passed"] else "TRAINED_CANDIDATE_FAILED_VALIDATION",
                    predictions_enabled=metrics["release_gate_passed"],blockers=metrics["release_gate_reasons"])
    if artifact["synthetic_mechanics_only"]:
        artifact.update(status="SYNTHETIC_MECHANICS_ONLY",predictions_enabled=False,
                        blockers=artifact["blockers"]+["SYNTHETIC_DATA_CANNOT_ESTABLISH_MARKET_FORECASTS"])
    artifact["model_sha256"]=canonical_hash(model)
    artifact["integrity_sha256"]=artifact_integrity(artifact)
    return artifact


def forecast(artifact: dict, row: dict, *, diagnostic: bool=False) -> dict:
    if not isinstance(artifact,dict) or not isinstance(row,dict):
        return {'status':'ABSTAIN','predictions':None,'orders_enabled':False,'blockers':['INVALID_ARTIFACT_OR_QUERY_SCHEMA']}
    result={"symbol":row.get("symbol"),"decision_at":row.get("decision_at"),"status":"ABSTAIN",
            "predictions":None,"orders_enabled":False,"blockers":[],"model_version":artifact.get("model_version")}
    if artifact.get('model_version')!=VERSION or artifact.get('schema_version')!='1.0':
        result['blockers']=['UNSUPPORTED_MODEL_OR_ARTIFACT_SCHEMA_VERSION'];return result
    result["blockers"]=row_reasons(row,artifact.get("research_mode"),training=False)
    try:
        if timestamp(row["decision_at"])<=timestamp(artifact["created_at"]):result["blockers"].append("ARTIFACT_NOT_AVAILABLE_AT_DECISION")
        if artifact.get('latest_information_used_at') and timestamp(row['decision_at'])<=timestamp(artifact['latest_information_used_at']):
            result['blockers'].append('VALIDATION_INFORMATION_NOT_AVAILABLE_AT_DECISION')
        if artifact.get('research_mode')=='OPERATIONAL_APP_REPLAY' and timestamp(row['decision_at'])<=timestamp(artifact['built_at']):
            result['blockers'].append('ARTIFACT_NOT_OPERATIONALLY_BUILT_AT_DECISION')
        if timestamp(row["decision_at"]).strftime("%H:%M")!=artifact["landmark_ist"]:result["blockers"].append("WRONG_LANDMARK_MODEL")
    except (ValueError,KeyError,TypeError,OverflowError):result["blockers"].append("INVALID_INFERENCE_CLOCK")
    model=artifact.get("model")
    if not model:result["blockers"].append("NO_FITTED_MODEL");return result
    expected=artifact_integrity(artifact)
    if artifact.get("integrity_sha256")!=expected:result["blockers"].append("ARTIFACT_INTEGRITY_MISMATCH")
    if sorted(row.get("features",{}))!=model["feature_names"]:result["blockers"].append("FEATURE_SCHEMA_MISMATCH")
    contract=artifact.get('feature_contract') or {}
    actual_contract={'feature_names':sorted(row.get('features',{})),
        'feature_variant':row.get('feature_variant','UNSPECIFIED_EXPLICIT_FEATURE_KEYS'),
        'registered_bar_lag_seconds':row.get('provenance',{}).get('registered_bar_lag_seconds'),
        'movement_band_bps':row.get('movement_band_bps'),
        'benchmark_instrument':row.get('provenance',{}).get('benchmark',{}).get('instrument')}
    if actual_contract!=contract:result['blockers'].append('INFERENCE_FEATURE_LAG_BAND_OR_BENCHMARK_CONTRACT_MISMATCH')
    if result["blockers"]:return result
    try:
        with np.errstate(over='raise',divide='raise',invalid='raise'):
            pred=raw_prediction(model,row,artifact["calibration"]["temperature"])
    except (ValueError,FloatingPointError,OverflowError):
        result["blockers"].append("UNSUPPORTED_NUMERICAL_RANGE_OR_NO_PRIOR_SUPPORT")
        return result
    cfg=artifact["configuration"]
    if pred["max_abs_standardized_feature"]>cfg["ood_z_limit"]:result["blockers"].append("OUT_OF_DISTRIBUTION_FEATURES")
    if pred["event_effective_sample_size"]<cfg["min_local_event_ess"]:result["blockers"].append("INSUFFICIENT_LOCAL_EVENT_SUPPORT")
    if pred["date_effective_sample_size"]<cfg["min_local_date_ess"]:result["blockers"].append("INSUFFICIENT_LOCAL_DATE_SUPPORT")
    if not artifact.get("predictions_enabled") or not artifact.get("test_metrics",{}).get("release_gate_passed"):
        result["blockers"].append("MODEL_FAILED_VALIDATION_RELEASE_GATE")
    if artifact.get("synthetic_mechanics_only"):
        result["blockers"].append("SYNTHETIC_DATA_CANNOT_ESTABLISH_MARKET_FORECASTS")
    details={"direction_probabilities":dict(zip(("DOWN","FLAT","UP"),pred["probabilities"])),
             "conditional_mean_bps":pred["mean"],"conditional_intervals":apply_intervals(pred,artifact["calibration"]),
             "movement_band_bps":row["movement_band_bps"],
             "event_effective_sample_size":pred["event_effective_sample_size"],
             "date_effective_sample_size":pred["date_effective_sample_size"],
             "reference_price":row["reference_price"],
             "interval_coverage_caveat":artifact["calibration"]["coverage_caveat"]}
    details["magnitude_percent_and_price_intervals"]={}
    try:
        for name,interval in details["conditional_intervals"].items():
            details["magnitude_percent_and_price_intervals"][name]={
                key.replace("_bps","_percent"):100*math.expm1(value/10000)
                for key,value in interval.items()}
            details["magnitude_percent_and_price_intervals"][name].update({
                key.replace("_bps","_reference_price"):row["reference_price"]*math.exp(value/10000)
                for key,value in interval.items()})
    except OverflowError:
        result["blockers"].append("MAGNITUDE_OUTSIDE_SUPPORTED_NUMERICAL_RANGE")
    if not result["blockers"]:
        result.update(status="RESEARCH_FORECAST",predictions=details)
    elif diagnostic:
        result["unvalidated_diagnostics"]=details
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    fit=sub.add_parser("train");fit.add_argument("dataset");fit.add_argument("--landmark",choices=LANDMARKS,default="09:40");fit.add_argument("--output",required=True)
    predict=sub.add_parser("forecast");predict.add_argument("artifact");predict.add_argument("row");predict.add_argument("--diagnostic",action="store_true");predict.add_argument("--output",required=True)
    args=parser.parse_args()
    if args.command=="train":result=train(json.loads(Path(args.dataset).read_text()),args.landmark)
    else:result=forecast(json.loads(Path(args.artifact).read_text()),json.loads(Path(args.row).read_text()),diagnostic=args.diagnostic)
    Path(args.output).write_text(json.dumps(result,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"status":result["status"],"output":args.output,"predictions_enabled":result.get("predictions_enabled",result.get("predictions") is not None),"blockers":result.get("blockers",[])}))


if __name__=="__main__":main()
