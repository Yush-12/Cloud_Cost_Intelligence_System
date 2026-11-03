"""Offline experiment evidence, scoped to the journal's account and region."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from costintel.store import SnapshotStore


def report(journal, store):
    events = [json.loads(line) for line in Path(journal).read_text(encoding='utf-8').splitlines() if line.strip()]
    if not events or events[0].get('event') != 'planned':
        raise ValueError('Missing experiment plan')
    plan = events[0]
    slots = plan['schedule']
    completed = {e['index']: e['result'] for e in events if e.get('event') == 'completed'}
    attempted = {e['index'] for e in events if e.get('event') == 'attempting'}
    account, region = plan['account'], plan['region']
    resource = f'arn:aws:lambda:{region}:{account}:function:costintel-lab-workload'
    history = store.history('local', 'aws', account, region, resource, 'Invocations', limit=2016)
    observations = {p['timestamp']: p['value'] for p in history
                    if p['statistic'] == 'Sum' and p['period_seconds'] == 300 and p['unit'] == 'Count'}
    rows = []
    for slot in slots:
        bucket = datetime.fromtimestamp(slot['at'] // 300 * 300, timezone.utc).isoformat()
        actual = observations.get(bucket)
        rows.append({'index': slot['index'], 'timestamp': bucket, 'scenario': slot['scenario'],
                     'planned_calls': slot['count'],
                     'journal_calls': completed.get(slot['index'], {}).get('invocations'),
                     'cloudwatch_calls': actual,
                     'matches_plan': actual == slot['count'] if actual is not None else None})
    return {'account': account, 'region': region, 'resource': resource,
            'state': 'finished' if events[-1]['event'] == 'finished' else
                     'stopped' if events[-1]['event'] == 'stopped' else 'in_progress_or_interrupted',
            'planned_calls': sum(s['count'] for s in slots),
            'confirmed_journal_calls': sum(r['invocations'] for r in completed.values()),
            'unconfirmed_attempts': sorted(attempted - completed.keys()),
            'observed_buckets': sum(r['cloudwatch_calls'] is not None for r in rows),
            'note': 'Missing observations are unknown. CloudWatch buckets may include other callers. '
                    'A journal alone does not prove a worker is still running.',
            'buckets': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', required=True)
    parser.add_argument('--database', default='.local-data/inventory.sqlite3')
    args = parser.parse_args()
    print(json.dumps(report(args.journal, SnapshotStore(args.database)), indent=2))


if __name__ == '__main__':
    main()
