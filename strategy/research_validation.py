"""Offline validation primitives. No promotion, network, or order execution.

Chronological folds operate on whole session keys; caller must supply verified
sessions and freeze the candidate set before inspecting held-out outcomes.
"""
from math import isfinite
from statistics import mean


def walk_forward_folds(sessions, train_size=60, test_size=20, embargo=1):
    for value in (train_size, test_size, embargo):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError('Fold sizes must be integers')
    if train_size < 1 or test_size < 1 or embargo < 0:
        raise ValueError('Invalid fold sizes')
    if list(sessions) != sorted(set(sessions)):
        raise ValueError('Sessions must be unique and chronological')
    folds=[]
    for start in range(train_size+embargo, len(sessions)-test_size+1, test_size):
        end=start-embargo
        folds.append({'train':list(sessions[end-train_size:end]),
                      'embargo':list(sessions[end:start]),
                      'test':list(sessions[start:start+test_size])})
    return folds


def select_on_training(training_returns):
    """Rank frozen candidates by mean net session return, including no-trade days.

    Training outcomes only. Caller must not feed test returns into this function.
    Equal sample lengths prevent comparing unlike coverage; no winner on no data.
    """
    if not training_returns: return None
    lengths={len(values) for values in training_returns.values()}
    if len(lengths)!=1 or not next(iter(lengths)):
        raise ValueError('Equal nonempty training coverage required')
    for values in training_returns.values():
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not isfinite(v) for v in values):
            raise ValueError('Finite net returns required')
    return min(training_returns, key=lambda key:(-mean(training_returns[key]),key))
