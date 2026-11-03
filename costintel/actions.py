"""Opt-in lab review tagging. No resource shutdown or savings claims."""
import argparse
from datetime import datetime, timezone
import hashlib
import math
import json

import boto3
from botocore.config import Config

from costintel.detection import analyze
from costintel.store import SnapshotStore

TAG = 'CostIntelReview'


def _plan_id(*parts):
    return hashlib.sha256(json.dumps(list(parts), sort_keys=True).encode()).hexdigest()


def review_plan(store, account, region, now=None):
    now = now or datetime.now(timezone.utc)
    resource = f'arn:aws:lambda:{region}:{account}:function:costintel-lab-workload'
    for result in analyze(store, 'local', 'aws', account, region):
        if result['resource_id'] != resource or result['metric'] != 'Duration' or result['status'] != 'outlier':
            continue
        age = (now - datetime.fromisoformat(result['sample_timestamp'])).total_seconds()
        # Historical evidence can request review; never use this policy to stop resources.
        if not 0 <= age <= 86400:
            continue
        evidence = {k: result[k] for k in ('metric', 'sample_timestamp', 'score', 'baseline_start', 'baseline_end')}
        key = _plan_id(account, region, resource, evidence)
        return {'id': key, 'resource': resource, 'account': account, 'region': region,
                'evidence': evidence, 'tag': {TAG: 'review-' + key[:24]}, 'action': 'tag_for_review',
                'est_monthly_savings_usd': None}
    return None


def apply_review(store, session, plan):
    config = Config(connect_timeout=5, read_timeout=10, retries={'total_max_attempts': 1})
    identity = session.client('sts', region_name=plan['region'], config=config).get_caller_identity()
    if identity['Account'] != plan['account'] or identity['Arn'].endswith(':root'):
        raise ValueError('Wrong account or root identity')
    expected = f"arn:aws:lambda:{plan['region']}:{plan['account']}:function:costintel-lab-workload"
    if plan['resource'] != expected or plan['region'] != 'ap-south-1':
        raise ValueError('Only the Mumbai lab is eligible')
    client = session.client('lambda', region_name=plan['region'], config=config)
    tags = client.list_tags(Resource=expected)['Tags']
    if tags.get('CostIntelLab') != 'true' or tags.get('AutoOptimize') != 'true':
        raise ValueError('Lab must explicitly opt in with AutoOptimize=true')
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS review_actions (id TEXT PRIMARY KEY, plan TEXT NOT NULL, status TEXT NOT NULL)')
        existing = db.execute('SELECT status FROM review_actions WHERE id=?', (plan['id'],)).fetchone()
        if existing:
            return {'status': existing[0], 'id': plan['id'], 'replayed': False}
        if TAG in tags:
            raise ValueError('Existing review tag requires human review; refusing to overwrite')
        db.execute('INSERT INTO review_actions VALUES (?, ?, ?)', (plan['id'], json.dumps(plan), 'attempting'))
    # Durable intent is committed before the API call. An uncertain result is never retried.
    try:
        client.tag_resource(Resource=expected, Tags=plan['tag'])
        actual = client.list_tags(Resource=expected)['Tags']
        if actual.get(TAG) != plan['tag'][TAG]:
            raise ValueError('Tag verification failed')
    except Exception:
        with store.connect() as db:
            db.execute('UPDATE review_actions SET status=? WHERE id=?', ('uncertain', plan['id']))
        raise
    with store.connect() as db:
        db.execute('UPDATE review_actions SET status=? WHERE id=?', ('applied', plan['id']))
    return {'status': 'applied', 'id': plan['id']}


def idle_ec2_plan(store, account, region, threshold_cpu=2.0, now=None):
    now = now or datetime.now(timezone.utc)
    snapshot = store.latest('local', 'aws', account, region)
    if not snapshot:
        return []
    plans = []
    for res in snapshot.get('resources', []):
        if res.get('service') != 'ec2' or res.get('state') != 'running':
            continue
        instance_id = res['resource_id']
        points = store.history('local', 'aws', account, region, instance_id, 'CPUUtilization')
        points = points[-12:]
        if len(points) < 12:
            continue
        times = [datetime.fromisoformat(p['timestamp']) for p in points]
        if not 0 <= (now-times[-1]).total_seconds() <= 900:
            continue
        if any((b-a).total_seconds() != 300 for a,b in zip(times,times[1:])):
            continue
        if any(p.get('statistic') != 'Average' or p.get('period_seconds') != 300 or
               p.get('unit') != 'Percent' or not math.isfinite(p['value']) or not 0 <= p['value'] <= 100 for p in points):
            continue
        avg_cpu = round(sum(p['value'] for p in points) / len(points), 4)
        if avg_cpu <= threshold_cpu:
            key = _plan_id(account, region, instance_id, 'stop_idle_instance', avg_cpu, points[-1]['timestamp'])
            plans.append({
                'id': key,
                'action': 'stop_idle_instance',
                'resource': instance_id,
                'account': account,
                'region': region,
                'reason': f'Average CPU utilization ({avg_cpu:.2f}%) below {threshold_cpu}% threshold across {len(points)} observations',
                'evidence': {
                    'metric': 'CPUUtilization',
                    'average': avg_cpu,
                    'threshold': threshold_cpu,
                    'sample_count': len(points),
                    'latest_timestamp': points[-1]['timestamp'] if points else None,
                },
                'target_state': 'stopped',
                'reversible': True,
                'est_monthly_savings_usd': None,
            })
    return plans


def apply_stop_instance(store, session, plan):
    config = Config(connect_timeout=5, read_timeout=10, retries={'total_max_attempts': 1})
    identity = session.client('sts', region_name=plan['region'], config=config).get_caller_identity()
    if identity['Account'] != plan['account'] or identity['Arn'].endswith(':root'):
        raise ValueError('Wrong account or root identity')
    if plan['region'] != 'ap-south-1':
        raise ValueError('Only Mumbai lab is eligible')
    ec2 = session.client('ec2', region_name=plan['region'], config=config)
    resp = ec2.describe_instances(InstanceIds=[plan['resource']])
    reservations = resp.get('Reservations', [])
    if not reservations or not reservations[0].get('Instances'):
        raise ValueError('Instance not found')
    instance = reservations[0]['Instances'][0]
    tags = {t['Key']: t['Value'] for t in instance.get('Tags', [])}
    if tags.get('CostIntelLab') != 'true' or tags.get('AutoOptimize') != 'true':
        raise ValueError('Instance must explicitly opt in with CostIntelLab=true and AutoOptimize=true')
    with store.connect() as db:
        if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='optimization_actions'").fetchone():
            existing = db.execute('SELECT status FROM optimization_actions WHERE id=?',(plan['id'],)).fetchone()
            if existing:
                return {'status':existing[0], 'id':plan['id'], 'replayed':False}
    if tags.get('CostIntelIdleStop') != 'true':
        raise ValueError('Explicit CostIntelIdleStop=true opt in is required')
    if not any(p['id']==plan['id'] for p in idle_ec2_plan(store,plan['account'],plan['region'])):
        raise ValueError('Idle evidence is stale or changed; collect again')
    current_state = instance.get('State', {}).get('Name')
    if current_state != 'running':
        raise ValueError(f'Instance is in state {current_state}, must be running to stop')
    with store.connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS optimization_actions 
                      (id TEXT PRIMARY KEY, plan TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)''')
        existing = db.execute('SELECT status FROM optimization_actions WHERE id=?', (plan['id'],)).fetchone()
        if existing:
            return {'status': existing[0], 'id': plan['id'], 'replayed': False}
        now_iso = datetime.now(timezone.utc).isoformat()
        db.execute('INSERT INTO optimization_actions VALUES (?, ?, ?, ?)',
                   (plan['id'], json.dumps(plan), 'attempting', now_iso))
    try:
        stop_resp = ec2.stop_instances(InstanceIds=[plan['resource']])
        stopping = stop_resp.get('StoppingInstances', [])
        if not stopping:
            raise ValueError('Stop instances command returned no state change')
        ec2.get_waiter('instance_stopped').wait(InstanceIds=[plan['resource']], WaiterConfig={'Delay':5,'MaxAttempts':24})
        if ec2.describe_instances(InstanceIds=[plan['resource']])['Reservations'][0]['Instances'][0]['State']['Name'] != 'stopped':
            raise ValueError('Stop state not verified')
    except Exception:
        with store.connect() as db:
            db.execute('UPDATE optimization_actions SET status=? WHERE id=?', ('uncertain', plan['id']))
        raise
    with store.connect() as db:
        db.execute('UPDATE optimization_actions SET status=? WHERE id=?', ('applied', plan['id']))
    return {'status': 'applied', 'id': plan['id'], 'replayed': False}


def apply_start_instance(store, session, action_id):
    config = Config(connect_timeout=5, read_timeout=10, retries={'total_max_attempts': 1})
    with store.connect() as db:
        row = db.execute('SELECT plan, status FROM optimization_actions WHERE id=?', (action_id,)).fetchone()
    if not row:
        raise ValueError('Unknown optimization action')
    plan = json.loads(row[0])
    if plan.get('action') != 'stop_idle_instance':
        raise ValueError('Only stopped instances can be restarted')
    identity = session.client('sts', region_name=plan['region'], config=config).get_caller_identity()
    if identity['Account'] != plan['account'] or identity['Arn'].endswith(':root'):
        raise ValueError('Wrong account or root identity')
    ec2 = session.client('ec2', region_name=plan['region'], config=config)
    resp = ec2.describe_instances(InstanceIds=[plan['resource']])
    instance = resp['Reservations'][0]['Instances'][0]
    tags = {t['Key']: t['Value'] for t in instance.get('Tags', [])}
    if tags.get('CostIntelLab') != 'true' or tags.get('AutoOptimize') != 'true':
        raise ValueError('Instance must explicitly opt in with CostIntelLab=true and AutoOptimize=true')
    if row[1] == 'restored':
        return {'status':'restored','id':action_id}
    current=instance.get('State',{}).get('Name')
    if current=='running' and row[1] in ('restoring','restore_uncertain'):
        with store.connect() as db:
            db.execute('UPDATE optimization_actions SET status=? WHERE id=?',('restored',action_id))
        return {'status':'restored','id':action_id}
    if current != 'stopped':
        raise ValueError('Resource state changed; refusing to overwrite')
    with store.connect() as db:
        db.execute('UPDATE optimization_actions SET status=? WHERE id=?', ('restoring', action_id))
    try:
        start_resp = ec2.start_instances(InstanceIds=[plan['resource']])
        starting = start_resp.get('StartingInstances', [])
        if not starting:
            raise ValueError('Start instances command returned no state change')
        ec2.get_waiter('instance_running').wait(InstanceIds=[plan['resource']], WaiterConfig={'Delay':5,'MaxAttempts':24})
        if ec2.describe_instances(InstanceIds=[plan['resource']])['Reservations'][0]['Instances'][0]['State']['Name'] != 'running':
            raise ValueError('Start state not verified')
    except Exception:
        with store.connect() as db:
            db.execute('UPDATE optimization_actions SET status=? WHERE id=?', ('restore_uncertain', action_id))
        raise
    with store.connect() as db:
        db.execute('UPDATE optimization_actions SET status=? WHERE id=?', ('restored', action_id))
    return {'status': 'restored', 'id': action_id}


def unused_volume_plan(store, account, region, now=None):
    snapshot = store.latest('local', 'aws', account, region)
    if not snapshot:
        return []
    plans = []
    for res in snapshot.get('resources', []):
        if res.get('service') == 'ebs' and res.get('state') == 'available' and res.get('attachments', 0) == 0:
            volume_id = res['resource_id']
            key = _plan_id(account, region, volume_id, 'tag_unused_volume')
            plans.append({
                'id': key,
                'action': 'tag_unused_volume',
                'resource': volume_id,
                'account': account,
                'region': region,
                'reason': 'Unattached EBS volume (state: available) incurring idle storage charges',
                'tag': {TAG: 'review-volume-' + key[:16]},
                'est_monthly_savings_usd': None,
            })
    return plans


def apply_tag_volume(store, session, plan):
    config = Config(connect_timeout=5, read_timeout=10, retries={'total_max_attempts': 1})
    identity = session.client('sts', region_name=plan['region'], config=config).get_caller_identity()
    if identity['Account'] != plan['account'] or identity['Arn'].endswith(':root'):
        raise ValueError('Wrong account or root identity')
    if plan['region'] != 'ap-south-1':
        raise ValueError('Only Mumbai lab is eligible')
    ec2 = session.client('ec2', region_name=plan['region'], config=config)
    resp = ec2.describe_volumes(VolumeIds=[plan['resource']])
    volumes = resp.get('Volumes', [])
    if not volumes:
        raise ValueError('Volume not found')
    volume = volumes[0]
    if volume.get('State') != 'available' or volume.get('Attachments'):
        raise ValueError('Volume is no longer unattached')
    tags = {t['Key']: t['Value'] for t in volume.get('Tags', [])}
    if tags.get('CostIntelLab') != 'true' or tags.get('AutoOptimize') != 'true':
        raise ValueError('Volume must explicitly opt in with CostIntelLab=true and AutoOptimize=true')
    with store.connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS optimization_actions 
                      (id TEXT PRIMARY KEY, plan TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)''')
        existing = db.execute('SELECT status FROM optimization_actions WHERE id=?', (plan['id'],)).fetchone()
        if existing:
            return {'status': existing[0], 'id': plan['id'], 'replayed': False}
        if TAG in tags:
            raise ValueError('Existing review tag requires human review; refusing to overwrite')
        now_iso = datetime.now(timezone.utc).isoformat()
        db.execute('INSERT INTO optimization_actions VALUES (?, ?, ?, ?)',
                   (plan['id'], json.dumps(plan), 'attempting', now_iso))
    try:
        tag_list = [{'Key': k, 'Value': v} for k, v in plan['tag'].items()]
        ec2.create_tags(Resources=[plan['resource']], Tags=tag_list)
        observed=ec2.describe_volumes(VolumeIds=[plan['resource']])['Volumes'][0]
        actual={t['Key']:t['Value'] for t in observed.get('Tags',[])}
        if any(actual.get(k)!=v for k,v in plan['tag'].items()):
            raise ValueError('Tag not verified')
    except Exception:
        with store.connect() as db:
            db.execute('UPDATE optimization_actions SET status=? WHERE id=?', ('uncertain', plan['id']))
        raise
    with store.connect() as db:
        db.execute('UPDATE optimization_actions SET status=? WHERE id=?', ('applied', plan['id']))
    return {'status': 'applied', 'id': plan['id'], 'replayed': False}


def generate_optimization_plans(store, account, region, now=None):
    plans = []
    rev = review_plan(store, account, region, now=now)
    if rev:
        plans.append(rev)
    for p in idle_ec2_plan(store, account, region, now=now):
        plans.append(p)
    for p in unused_volume_plan(store, account, region, now=now):
        plans.append(p)
    return plans


def apply_optimization_plan(store, session, plan):
    action = plan.get('action')
    if action == 'tag_for_review':
        return apply_review(store, session, plan)
    elif action == 'stop_idle_instance':
        return apply_stop_instance(store, session, plan)
    elif action == 'tag_unused_volume':
        return apply_tag_volume(store, session, plan)
    raise ValueError(f'Unknown optimization action: {action}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    parser.add_argument('--region', default='ap-south-1')
    parser.add_argument('--database', default='.local-data/inventory.sqlite3')
    parser.add_argument('--profile')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--action-type', choices=['all', 'review', 'stop-ec2', 'tag-volume'], default='all')
    args = parser.parse_args()
    store = SnapshotStore(args.database)
    if args.action_type == 'review':
        rev = review_plan(store, args.account, args.region)
        plans = [rev] if rev else []
    elif args.action_type == 'stop-ec2':
        plans = idle_ec2_plan(store, args.account, args.region)
    elif args.action_type == 'tag-volume':
        plans = unused_volume_plan(store, args.account, args.region)
    else:
        plans = generate_optimization_plans(store, args.account, args.region)

    if not args.apply:
        print(json.dumps({'mode': 'dry_run', 'plans': plans, 'count': len(plans)}, indent=2))
        return
    if not args.profile:
        parser.error('--apply requires --profile')
    session = boto3.Session(profile_name=args.profile)
    results = [apply_optimization_plan(store, session, p) for p in plans]
    print(json.dumps({'mode': 'applied', 'results': results, 'count': len(results)}, indent=2))


if __name__ == '__main__':
    main()
