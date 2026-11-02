"""Manual lab demonstration: one baseline probe, one blocked probe, one restored probe."""
import argparse
import json
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from costintel.containment import apply, client_for, initialize, prepare, restore
from costintel.store import SnapshotStore
from scripts.baseline_experiment import append


def probe(client, arn):
    try:
        response = client.invoke(FunctionName=arn, InvocationType='RequestResponse',
                                 Payload=b'{"scenario":"baseline"}')
        response['Payload'].close()
        if response.get('StatusCode') != 200 or response.get('FunctionError'):
            raise ValueError('Probe failed')
        return 'executed'
    except ClientError as exc:
        if exc.response['Error']['Code'] == 'TooManyRequestsException':
            return 'throttled'
        raise


def demonstrate(store, client, account, journal):
    journal = Path(journal)
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open('x', encoding='utf-8') as stream:
        stream.write('{"event":"started"}\n')
    initialize(store)
    plan = prepare(client, account)
    if not plan['opted_in'] or plan['previous'] == 0:
        raise ValueError('Lab must be opted in and enabled')
    with store.connect() as db:
        if db.execute("SELECT id FROM containment_actions WHERE status != 'restored'").fetchone():
            raise ValueError('Resolve existing action before probing')
        old_ids = {r[0] for r in db.execute('SELECT id FROM containment_actions')}
    before = probe(client, plan['resource'])
    append(journal, {'event': 'before', 'result': before})
    if before != 'executed':
        raise ValueError('Initial probe must succeed')
    try:
        action = apply(store, client, account)
        append(journal, {'event': 'contained', 'action': action})
        during = probe(client, plan['resource'])
        append(journal, {'event': 'during', 'result': during})
        if during != 'throttled':
            raise ValueError('Containment probe was not throttled')
    finally:
        # Even a lost apply response retains intent and the previous state.
        with store.connect() as db:
            new_ids = [r[0] for r in db.execute('SELECT id FROM containment_actions') if r[0] not in old_ids]
        for action_id in new_ids:
            result = restore(store, client, account, action_id)
            append(journal, {'event': 'restored', **result})
    after = probe(client, plan['resource'])
    append(journal, {'event': 'after', 'result': after})
    if after != 'executed':
        raise ValueError('Post-restore probe failed')
    append(journal, {'event': 'finished', 'note': 'Availability control verified; no billed savings claimed.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--account', required=True)
    parser.add_argument('--journal', default='.local-data/experiments/containment.jsonl')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    client = client_for(boto3.Session(profile_name=args.profile), args.account)
    if not args.execute:
        print(json.dumps(prepare(client, args.account), indent=2))
        return
    demonstrate(SnapshotStore('.local-data/inventory.sqlite3'), client, args.account, args.journal)


if __name__ == '__main__':
    main()
