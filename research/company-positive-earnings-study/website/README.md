# Read-only website inference bridge

`forecast_bridge.py` supplies `model_status(registry)` and `predict_from_snapshot(snapshot, registry, engine_path=...)`. It makes no market requests, accesses no credentials, and cannot place orders. The current real-data artifacts remain untrained; their response is an unavailable forecast.

The original app folder is outside this task's writable roots and **has not been edited**. This folder is a reviewable integration module. The prior positive-results panel installer is a separate package; this new model is not silently installed into that panel.

## Runtime and files

The actual website virtual environment has no NumPy. The bundled research Python has the tested NumPy 2.3.5. On a deployment environment the owner controls, install that tested dependency into its virtual environment before enabling fitted inference:

```sh
./.venv/bin/python -m pip install 'numpy==2.3.5'
```

Keep `positive_path_model.py` and **`feature_snapshot.py` together** in a server-owned model-code directory. Pass the engine file path as server configuration. Registry directories and paths must not come from browser query parameters. Missing dependencies fail closed, and disabled/missing model artifacts do not import NumPy.

## Registry and response

Use a server-owned registry containing `09-30.json`, `09-40.json`, `10-00.json`, and `10-30.json`, each from the corresponding real model's `train` command. Do not rename a model from another landmark or change its validation flags. The forecast engine verifies artifact integrity, version, information clocks, feature contract, source lineage, validation evidence, input support and drift bounds. Merely creating a registry file cannot certify historical accuracy.

The HTTP handler should obtain the selected company's immutable market/filing snapshot from the existing backend, then call the bridge. It may return the resulting object alongside its existing company detail. Never accept quality attestations or source clocks directly from an untrusted browser as proof of original financial availability.

```python
from forecast_bridge import predict_from_snapshot

prediction = predict_from_snapshot(
    snapshot_from_existing_backend,
    registry=server_owned_registry,
    engine_path=server_owned_model_code / 'positive_path_model.py',
)
```

The backend snapshot has:

- `row`: company/event identity, announcement and next-session date, decision timestamp, positive-financial eligibility, movement band, quality attestations and genuine public/operational provenance matching `model/dataset.schema.json`.
- `current`, `previous`: comparable positive quarterly facts with start/end, unit, basis, revenue/PAT definitions and figures.
- `bars`: **the selected company's** five-minute start-stamped `ts/o/h/l/c`, with `available_at` when operational arrivals are known. Only the completed usable prefix is required; no 11:00 outcome is needed.
- Optional `benchmark_bars`: a separately identified/verified market context prefix with matching timestamps. It never replaces company prices. The engine requires that variant's benchmark lineage.

The bridge overwrites feature vectors, reference prices, variant labels and prefix timestamps with the shared causal feature builder; it removes any supplied future targets before inference. Source/financial verification is preserved rather than invented. The model's feature-lag and movement-band contract must match the query.

Validated output can include DOWN/FLAT/UP probabilities, future maximum-rise and maximum-fall intervals, remaining checkpoint/11:00 return intervals, percentage and rupee reference levels, sample-support measures and model provenance. Otherwise `predictions` is null with a clear blocker. Missing probability is not 0%, and a conditional median is not a guaranteed future price.

Use the endpoint for a selected company, or a bounded backend batch with a cached server model registry. Avoid rereading multi-megabyte training artifacts for every company on every browser tick. No fitting or backfilling should run in an HTTP request.

## Verification

```sh
python3 -B -m unittest discover -s website -p 'test_*.py' -v
```

Run from the package root. The full real-engine integration test requires NumPy and otherwise reports an explicit skip. The bundled research runtime runs all tests. No real-market fitted artifact or live website deployment has been tested or claimed.
