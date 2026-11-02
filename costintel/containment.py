"""Explicit sandbox containment with durable before-state and verified restoration."""
import argparse
import json
import uuid
from datetime import datetime, timezone

import boto3
from botocore.config import Config

from costintel.store import SnapshotStore


def client_for(session, account):
    config = Config(connect_timeout=5, read_timeout=10, retries={'total_max_attempts': 1})
    identity = session.client('sts', region_name='ap-south-1', config=config).get_caller_identity()
    if identity['Account'] != account or identity['Arn'].endswith(':root'):
        raise ValueError('Wrong account or root identity')
    return session.client('lambda', region_name='ap-south-1', config=config)


def prepare(client, account):
    arn = f'arn:aws:lambda:ap-south-1:{account}:function:costintel-lab-workload'
    function = client.get_function(FunctionName=arn)
    config = function['Configuration']
    if config['FunctionArn'] != arn or config.get('State') != 'Active' or config.get('LastUpdateStatus') != 'Successful':
        raise ValueError('Lab is not active and stable')
    if config.get('MemorySize') != 128 or config.get('Timeout') != 3:
        raise ValueError('Lab runtime bounds changed')
    if function.get('Tags', {}).get('CostIntelLab') != 'true':
        raise ValueError('Not a tagged lab')
    previous = client.get_function_concurrency(FunctionName=arn).get('ReservedConcurrentExecutions')
    return {'account': account, 'resource': arn, 'previous': previous, 'target': 0,
            'opted_in': function.get('Tags', {}).get('CostIntelContainment') == 'true',
            'effect': 'Blocks new executions; not a measured savings estimate'}


def initialize(store):
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS containment_actions '
                   '(id TEXT PRIMARY KEY, plan TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)')


def status(store, action_id, value):
    with store.connect() as db:
        db.execute('UPDATE containment_actions SET status=? WHERE id=?', (value, action_id))


def apply(store, client, account):
    initialize(store)
    plan = prepare(client, account)
    if not plan['opted_in'] or plan['previous'] == 0:
        raise ValueError('Explicit containment opt-in required; lab must not already be disabled')
    action_id = str(uuid.uuid4())
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        active = db.execute("SELECT id FROM containment_actions WHERE status != 'restored'").fetchall()
        # Single local executor. Never overlap uncertain actions, even across accounts.
        if active:
            raise ValueError('Resolve or restore existing containment action first')
        db.execute('INSERT INTO containment_actions VALUES (?, ?, ?, ?)',
                   (action_id, json.dumps(plan), 'attempting', datetime.now(timezone.utc).isoformat()))
    try:
        client.put_function_concurrency(FunctionName=plan['resource'], ReservedConcurrentExecutions=0)
        actual = client.get_function_concurrency(FunctionName=plan['resource']).get('ReservedConcurrentExecutions')
        if actual != 0:
            raise ValueError('Containment not verified')
    except Exception:
        status(store, action_id, 'uncertain')
        raise
    status(store, action_id, 'contained')
    return {'id': action_id, 'status': 'contained', **plan}


def restore(store, client, account, action_id):
    initialize(store)
    with store.connect() as db:
        row = db.execute('SELECT plan, status FROM containment_actions WHERE id=?', (action_id,)).fetchone()
    if not row:
        raise ValueError('Unknown action')
    plan, state = json.loads(row[0]), row[1]
    expected = f'arn:aws:lambda:ap-south-1:{account}:function:costintel-lab-workload'
    if plan['account'] != account or plan['resource'] != expected:
        raise ValueError('Wrong restore account or resource')
    if state == 'restored':
        return {'id': action_id, 'status': state}
    actual = client.get_function_concurrency(FunctionName=expected).get('ReservedConcurrentExecutions')
    if actual == plan['previous']:
        status(store, action_id, 'restored')
        return {'id': action_id, 'status': 'restored'}
    if actual != 0:
        raise ValueError('Concurrency changed externally; refusing to overwrite')
    status(store, action_id, 'restoring')
    try:
        if plan['previous'] is None:
            client.delete_function_concurrency(FunctionName=expected)
        else:
            client.put_function_concurrency(FunctionName=expected, ReservedConcurrentExecutions=plan['previous'])
        actual = client.get_function_concurrency(FunctionName=expected).get('ReservedConcurrentExecutions')
        if actual != plan['previous']:
            raise ValueError('Restoration not verified')
    except Exception:
        status(store, action_id, 'restore_uncertain')
        raise
    status(store, action_id, 'restored')
    return {'id': action_id, 'status': 'restored'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['preview', 'apply', 'restore'])
    parser.add_argument('--profile', required=True)
    parser.add_argument('--account', required=True)
    parser.add_argument('--database', default='.local-data/inventory.sqlite3')
    parser.add_argument('--action-id')
    args = parser.parse_args()
    if args.command == 'restore' and not args.action_id:
        parser.error('restore requires --action-id')
    store = SnapshotStore(args.database)
    client = client_for(boto3.Session(profile_name=args.profile), args.account)
    result = (prepare(client, args.account) if args.command == 'preview' else
              apply(store, client, args.account) if args.command == 'apply' else
              restore(store, client, args.account, args.action_id))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
