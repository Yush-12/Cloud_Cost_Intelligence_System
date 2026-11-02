"""Experimental metric outlier detection; scores are not cost or probabilities."""
import math
from datetime import datetime

MIN_BASELINE = 36


def detect(points):
    # Use a continuous tail; missing observations must not become zeroes.
    ordered = sorted(points, key=lambda p: p['timestamp'])
    tail = []
    for point in ordered:
        if not math.isfinite(point['value']):
            tail = []
            continue
        if tail:
            previous = tail[-1]
            delta = (datetime.fromisoformat(point['timestamp']) -
                     datetime.fromisoformat(previous['timestamp'])).total_seconds()
            if delta != point['period_seconds'] or any(
                point[k] != previous[k] for k in ('unit', 'statistic', 'period_seconds')
            ):
                tail = []
        tail.append(point)
    result = {'status': 'insufficient_data', 'baseline_points': max(0, len(tail) - 1),
              'required_baseline_points': MIN_BASELINE, 'score': None,
              'sample_timestamp': tail[-1]['timestamp'] if tail else None}
    if len(tail) <= MIN_BASELINE:
        return result
    from sklearn.ensemble import IsolationForest
    values = [[p['value']] for p in tail[:-1]]
    if len({v[0] for v in values}) < 3:
        return {**result, 'status': 'insufficient_variation'}
    model = IsolationForest(n_estimators=100, contamination=0.05, random_state=42, n_jobs=1)
    model.fit(values)
    score = float(model.decision_function([[tail[-1]['value']]])[0])
    return {**result, 'status': 'outlier' if score < 0 else 'within_baseline',
            'score': score, 'baseline_start': tail[0]['timestamp'],
            'baseline_end': tail[-2]['timestamp']}


def analyze(store, owner, mode, account, region):
    snapshot = store.latest(owner, mode, account, region)
    results = []
    for series in (snapshot or {}).get('telemetry', {}).get('series', []):
        points = store.history(owner, mode, account, region, series['resource_id'], series['metric'])
        results.append({'resource_id': series['resource_id'], 'metric': series['metric'],
                        'points': points, **detect(points)})
    return results
