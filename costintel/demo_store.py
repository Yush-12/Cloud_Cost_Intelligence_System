"""Stateless, synthetic website tour. Never connects to AWS or writes user data."""
from statistics import median

from costintel.detection import detect
from costintel.inventory import collect_demo


class DemoStore:
    def __init__(self, region='ap-south-1'):
        self.snapshot = collect_demo(region)

    def save(self, owner, snapshot):
        if snapshot.get('mode') != 'demo':
            raise ValueError('Demo store accepts synthetic snapshots only')
        self.snapshot = snapshot
        return 'simulated-snapshot'

    def latest(self, owner, mode, account_id, region):
        return self.snapshot if mode == 'demo' and account_id == 'demo-account' else None

    def history(self, owner, mode, account_id, region, resource_id, metric, limit=288):
        if mode != 'demo' or account_id != 'demo-account':
            return []
        for series in self.snapshot['telemetry']['series']:
            if series['resource_id'] == resource_id and series['metric'] == metric:
                return [{**point, 'unit': series['unit'], 'statistic': series['statistic'],
                         'period_seconds': series['period_seconds']} for point in series['points'][-limit:]]
        return []

    def review_history(self, owner, mode, account_id, region):
        return [{'id': 'demo-review-001', 'action': 'tag_for_review',
                 'resource': 'demo-lambda-workload', 'status': 'applied_simulated',
                 'created_at': self.snapshot['collected_at']}]

    def containment_history(self, owner, mode, account_id, region):
        series = self.snapshot['telemetry']['series'][0]
        points = self.history(owner, mode, account_id, region, series['resource_id'], series['metric'])
        result = detect(points)
        return [{'id': 'demo-containment-001', 'resource': 'demo-lambda-workload',
                 'status': 'restored', 'simulated': True, 'previous': None,
                 'created_at': self.snapshot['collected_at'],
                 'evidence': {'baseline_median_ms': median(p['value'] for p in points[:-1]),
                              'sample_ms': points[-1]['value'], 'baseline_points': len(points)-1,
                              'score': result['score'], 'sample_timestamp': points[-1]['timestamp']}}]

    def optimization_history(self, owner, mode, account_id, region):
        return []
