"""Read-only bridge for the existing dashboard's positive-earnings panel.

Registry and engine paths are server configuration, never browser parameters.
No external requests, credential access, model training, trade or order calls.
NumPy is imported only when an artifact has passed its validation screen.
"""
from __future__ import annotations
import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST=timezone(timedelta(hours=5,minutes=30))
LANDMARKS={'09:30','09:40','10:00','10:30'}
MAX_ARTIFACT_BYTES=32*1024*1024


def _stamp(raw):
    if not isinstance(raw,str):raise ValueError('Timezone-aware timestamp required')
    value=datetime.fromisoformat(raw.replace('Z','+00:00'))
    if value.tzinfo is None:raise ValueError('Timezone-aware timestamp required')
    return value.astimezone(IST)


def _read_artifact(registry,landmark):
    if landmark not in LANDMARKS:raise ValueError('Unsupported model landmark')
    root=Path(registry).resolve()
    path=root/(landmark.replace(':','-')+'.json')
    if path.is_symlink():raise ValueError('Registry artifacts must be regular files')
    if not path.exists():return None
    if not path.is_file() or path.stat().st_size>MAX_ARTIFACT_BYTES:
        raise ValueError('Invalid or oversized registry artifact')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict):raise ValueError('Invalid artifact object')
    return value


def _engine(path=None):
    target=Path(path or Path(__file__).resolve().parent.parent/'model'/'positive_path_model.py')
    if not target.is_file():raise ImportError('Model engine is not installed')
    spec=importlib.util.spec_from_file_location('_zien_positive_path_engine',target)
    if spec is None or spec.loader is None:raise ImportError('Model engine is not installed')
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    feature_path=target.with_name('feature_snapshot.py')
    if not feature_path.is_file():raise ImportError('Shared feature builder is not installed')
    feature_spec=importlib.util.spec_from_file_location('_zien_positive_feature_snapshot',feature_path)
    if feature_spec is None or feature_spec.loader is None:raise ImportError('Feature builder unavailable')
    feature_module=importlib.util.module_from_spec(feature_spec)
    sys.modules[feature_spec.name]=feature_module
    feature_spec.loader.exec_module(feature_module)
    module.build_feature_snapshot=feature_module.build_feature_snapshot
    return module


def model_status(registry):
    result={'orders_enabled':False,'data_source':'Server model registry','models':{}}
    for landmark in sorted(LANDMARKS):
        try:
            artifact=_read_artifact(registry,landmark)
            result['models'][landmark]={'status':artifact.get('status') if artifact else 'MODEL_NOT_TRAINED',
              'validation_flag_present':bool(artifact and artifact.get('predictions_enabled')),
              'created_at':artifact.get('created_at') if artifact else None,
              'model_version':artifact.get('model_version') if artifact else None,
              'note':'A registry status is not a company prediction; inference rechecks all clocks, lineage and support.'}
        except (OSError,ValueError,TypeError):
            result['models'][landmark]={'status':'MODEL_REGISTRY_UNAVAILABLE'}
    return result


def predict_from_snapshot(snapshot,registry,*,engine_path=None,engine=None):
    """Return forecast/abstention from an immutable server-supplied snapshot.

    snapshot contains `row` with model inference metadata/quality/provenance,
    `current` and `previous` comparable quarter facts, `bars` of that company's
    own OHLC and optional `benchmark_bars` as separate market context.
    Caller provides genuine source metadata; this bridge never certifies it.
    """
    result={'status':'ABSTAIN','predictions':None,'orders_enabled':False,'blockers':[]}
    try:
        if not isinstance(snapshot,dict) or not isinstance(snapshot.get('row'),dict):
            raise ValueError('Snapshot row required')
        row=dict(snapshot['row'])
        sym=row.get('symbol','')
        if not isinstance(sym,str) or not re.fullmatch(r'[A-Z0-9][A-Z0-9&_.-]{0,29}',sym):
            raise ValueError('Company symbol required')
        decision=_stamp(row['decision_at'])
        landmark=decision.strftime('%H:%M')
        if landmark not in LANDMARKS or decision.second or decision.microsecond:
            raise ValueError('Registered decision landmark required')
        result.update(symbol=sym,decision_at=decision.isoformat())
        artifact=_read_artifact(registry,landmark)
        if artifact is None:
            result.update(status='MODEL_NOT_TRAINED',blockers=['NO_FITTED_VALIDATED_MODEL_FOR_LANDMARK'])
            return result
        if not artifact.get('model') or not artifact.get('predictions_enabled'):
            result.update(status='MODEL_NOT_VALIDATED',blockers=artifact.get('blockers') or ['MODEL_NOT_VALIDATED'],
                          model_version=artifact.get('model_version'))
            return result
        loaded=engine or _engine(engine_path)
        if not isinstance(snapshot.get('current'),dict) or not isinstance(snapshot.get('previous'),dict):
            raise ValueError('Comparable financial objects required')
        if not isinstance(snapshot.get('bars'),list) or any(not isinstance(b,dict) for b in snapshot['bars']):
            raise ValueError('Company candle list required')
        if snapshot.get('benchmark_bars') is not None and (not isinstance(snapshot['benchmark_bars'],list)
                or any(not isinstance(b,dict) for b in snapshot['benchmark_bars'])):
            raise ValueError('Separate benchmark candle list required')
        provenance=dict(row.get('provenance') or {})
        lag=provenance.get('registered_bar_lag_seconds',0)
        features=loaded.build_feature_snapshot(snapshot['current'],snapshot['previous'],
            snapshot['bars'],row['decision_at'],lag_seconds=lag,
            benchmark_bars=snapshot.get('benchmark_bars'))
        row.update(features=features['features'],reference_price=features['reference_price'])
        row['feature_variant']=features.get('feature_variant',row.get('feature_variant'))
        row['reference_kind']=features.get('reference_kind','last_completed_close_under_registered_bar_lag')
        row.pop('targets',None)
        row.pop('target_available_at',None)
        provenance.update(last_feature_bar_end=features['last_feature_bar_end'],registered_bar_lag_seconds=lag)
        if features.get('benchmark_last_feature_bar_end'):
            benchmark_provenance=dict(provenance.get('benchmark') or {})
            benchmark_provenance['last_feature_bar_end']=features['benchmark_last_feature_bar_end']
            provenance['benchmark']=benchmark_provenance
        row['provenance']=provenance
        feature_clock=_stamp(features['last_feature_bar_end'])+timedelta(seconds=lag)
        dependencies=[feature_clock,_stamp(provenance['original_publication_at']),
                      _stamp(provenance['financial_version_available_at'])]
        if row.get('features_known_at'):dependencies.append(_stamp(row['features_known_at']))
        row['features_known_at']=max(dependencies).isoformat()
        # forecast() verifies source quality, original versions, model integrity,
        # artifact availability, landmark, feature variant, lag and local support.
        response=loaded.forecast(artifact,row)
        response['orders_enabled']=False
        return response
    except ImportError:
        result.update(status='DEPENDENCY_MISSING',blockers=['NUMPY_OR_MODEL_MODULE_NOT_INSTALLED'])
    except (OSError,ValueError,KeyError,TypeError,ArithmeticError,AttributeError,IndexError):
        result.update(status='SNAPSHOT_UNAVAILABLE',blockers=['INVALID_OR_INCOMPLETE_MODEL_SNAPSHOT'])
    return result
