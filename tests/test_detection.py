from datetime import datetime, timedelta, timezone

from costintel.detection import detect


def points(values):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [dict(timestamp=(start + timedelta(minutes=5*i)).isoformat(),
                 value=v, unit='Milliseconds', statistic='Average', period_seconds=300)
            for i, v in enumerate(values)]


def test_requires_prior_observations():
    assert detect([])['status'] == 'insufficient_data'
    assert detect(points([50]*36))['status'] == 'insufficient_data'


def test_gap_and_statistic_change_reset_baseline():
    data = points([49, 50, 51]*20)
    del data[-3]
    assert detect(data)['baseline_points'] == 1
    data = points([49, 50, 51]*20)
    data[-1]['statistic'] = 'Maximum'
    assert detect(data)['status'] == 'insufficient_data'


def test_constant_baseline_does_not_claim_model_evidence():
    assert detect(points([50]*40+[500]))['status'] == 'insufficient_variation'


def test_held_out_spike_and_normal_observation():
    from random import Random
    rng = Random(42)
    baseline = [rng.gauss(50, 3) for _ in range(66)]
    normal = detect(points(baseline+[50]))
    spike = detect(points(baseline+[500]))
    assert normal['status'] == 'within_baseline'
    assert spike['status'] == 'outlier'
    assert spike['baseline_points'] == 66
    assert spike['baseline_end'] < spike['sample_timestamp']
    assert spike == detect(points(baseline+[500]))


def test_discrete_baseline_limitation_is_visible():
    baseline = [45 + (i % 11) for i in range(66)]
    assert detect(points(baseline+[50]))['status'] == 'within_baseline'
    # Trees cannot extrapolate beyond training extrema; repeated edge values
    # can put an extreme unseen value exactly on the learned boundary.
    assert detect(points(baseline+[500]))['status'] == 'within_baseline'


def test_dashboard_and_api_scope(tmp_path):
    from costintel.store import SnapshotStore
    from costintel.web import create_app
    store = SnapshotStore(tmp_path / 'observations.db')
    data = {'mode': 'aws', 'account_id': '123456789012', 'region': 'ap-south-1',
            'collected_at': datetime.now(timezone.utc).isoformat(), 'resources': [],
            'telemetry': {'window_start': 'start', 'window_end': 'end',
                          'collected_resources': 1, 'skipped_resources': 0,
                          'series': [dict(resource_id='lab', metric='Duration',
                                          statistic='Average', period_seconds=300,
                                          unit='Milliseconds', points=points([50]),
                                          latest=points([50])[0], expected_points=12)]}}
    store.save('local', data)
    client = create_app(store, 'local', 'aws', '123456789012', 'ap-south-1').test_client()
    assert client.get('/').status_code == 200
    result = client.get('/api/detection?account=other').json
    assert result['results'][0]['status'] == 'insufficient_data'
    assert result['optimization_enabled'] is False
    other = create_app(store, 'someone-else', 'aws', '123456789012', 'ap-south-1').test_client()
    assert other.get('/api/detection').json['results'] == []
