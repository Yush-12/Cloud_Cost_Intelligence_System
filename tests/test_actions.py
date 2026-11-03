from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from costintel.actions import apply_review, TAG
from costintel.store import SnapshotStore


def setup(tmp_path):
    store = SnapshotStore(tmp_path / 'actions.db')
    session, sts, client = Mock(), Mock(), Mock()
    session.client.side_effect = lambda name, **kw: sts if name == 'sts' else client
    sts.get_caller_identity.return_value = {'Account': '123456789012', 'Arn': 'arn:aws:iam::123456789012:user/test'}
    plan = {'id': 'action-1', 'account': '123456789012', 'region': 'ap-south-1',
            'resource': 'arn:aws:lambda:ap-south-1:123456789012:function:costintel-lab-workload',
            'tag': {TAG: 'review-action-1'}}
    client.list_tags.return_value = {'Tags': {'CostIntelLab': 'true', 'AutoOptimize': 'true'}}
    return store, session, sts, client, plan


def test_opt_out_and_wrong_account_never_write(tmp_path):
    store, session, sts, client, plan = setup(tmp_path)
    client.list_tags.return_value['Tags']['AutoOptimize'] = 'false'
    with pytest.raises(ValueError, match='opt in'):
        apply_review(store, session, plan)
    sts.get_caller_identity.return_value['Account'] = 'other'
    with pytest.raises(ValueError, match='Wrong account'):
        apply_review(store, session, plan)
    client.tag_resource.assert_not_called()


def test_applied_action_is_not_replayed(tmp_path):
    store, session, _, client, plan = setup(tmp_path)
    def tag(**kw):
        client.list_tags.return_value['Tags'].update(kw['Tags'])
    client.tag_resource.side_effect = tag
    assert apply_review(store, session, plan)['status'] == 'applied'
    assert apply_review(store, session, plan)['replayed'] is False
    assert client.tag_resource.call_count == 1
    assert len(store.review_history('local', 'aws', plan['account'], plan['region'])) == 1
    assert store.review_history('other', 'aws', plan['account'], plan['region']) == []
    assert store.review_history('local', 'aws', 'other', plan['region']) == []


def test_uncertain_action_is_not_replayed(tmp_path):
    store, session, _, client, plan = setup(tmp_path)
    client.tag_resource.side_effect = RuntimeError('connection lost')
    with pytest.raises(RuntimeError):
        apply_review(store, session, plan)
    assert apply_review(store, session, plan)['status'] == 'uncertain'
    assert client.tag_resource.call_count == 1


def test_never_overwrites_existing_review_tag(tmp_path):
    store, session, _, client, plan = setup(tmp_path)
    client.list_tags.return_value['Tags'][TAG] = 'another-review'
    with pytest.raises(ValueError, match='overwrite'):
        apply_review(store, session, plan)
    client.tag_resource.assert_not_called()


def test_idle_ec2_plan_generation(tmp_path):
    from costintel.actions import idle_ec2_plan
    store = SnapshotStore(tmp_path / 'actions.db')
    account, region, inst_id = '123456789012', 'ap-south-1', 'i-0abcdef1234567890'
    snap = {
        'mode': 'aws', 'account_id': account, 'region': region,
        'resources': [{'resource_id': inst_id, 'service': 'ec2', 'state': 'running', 'instance_type': 't2.micro'}],
        'telemetry': {'series': [{
            'resource_id': inst_id, 'service': 'ec2', 'metric': 'CPUUtilization',
            'statistic': 'Average', 'unit': 'Percent', 'period_seconds': 300,
            'points': [{'timestamp': (datetime.now(timezone.utc).replace(second=0,microsecond=0)-timedelta(minutes=5*m)).isoformat(), 'value':0.8} for m in range(12,0,-1)]
        }]}
    }
    store.save('local', snap)
    plans = idle_ec2_plan(store, account, region, threshold_cpu=2.0)
    assert len(plans) == 1
    assert plans[0]['action'] == 'stop_idle_instance'
    assert plans[0]['resource'] == inst_id
    assert plans[0]['target_state'] == 'stopped'
    assert plans[0]['evidence']['average'] == pytest.approx(0.8)
    assert plans[0]['est_monthly_savings_usd'] is None

    # When CPU is high, no idle plan generated
    snap_busy = {
        'mode': 'aws', 'account_id': account, 'region': region,
        'resources': [{'resource_id': inst_id, 'service': 'ec2', 'state': 'running', 'instance_type': 't2.micro'}],
        'telemetry': {'series': [{
            'resource_id': inst_id, 'service': 'ec2', 'metric': 'CPUUtilization',
            'statistic': 'Average', 'unit': 'Percent', 'period_seconds': 300,
            'points': [{'timestamp':p['timestamp'],'value':45.0} for p in snap['telemetry']['series'][0]['points']]
        }]}
    }
    store.save('local', snap_busy)
    assert idle_ec2_plan(store, account, region, threshold_cpu=2.0) == []


def test_apply_stop_and_start_instance(tmp_path):
    from costintel.actions import apply_stop_instance, apply_start_instance
    store = SnapshotStore(tmp_path / 'actions.db')
    account, region, inst_id = '123456789012', 'ap-south-1', 'i-0abcdef1234567890'
    session, sts, ec2 = Mock(), Mock(), Mock()
    session.client.side_effect = lambda name, **kw: sts if name == 'sts' else ec2
    sts.get_caller_identity.return_value = {'Account': account, 'Arn': f'arn:aws:iam::{account}:user/test'}
    ec2.describe_instances.return_value = {
        'Reservations': [{'Instances': [{
            'InstanceId': inst_id,
            'State': {'Name': 'running'},
            'Tags': [{'Key': 'CostIntelLab', 'Value': 'true'}, {'Key': 'AutoOptimize', 'Value': 'true'}]
        }]}]
    }
    ec2.stop_instances.return_value = {'StoppingInstances': [{'InstanceId': inst_id, 'CurrentState': {'Name': 'stopping'}}]}
    ec2.start_instances.return_value = {'StartingInstances': [{'InstanceId': inst_id, 'CurrentState': {'Name': 'pending'}}]}

    plan = {
        'id': 'plan-stop-1', 'action': 'stop_idle_instance', 'account': account,
        'region': region, 'resource': inst_id, 'target_state': 'stopped', 'reversible': True
    }

    from costintel.actions import idle_ec2_plan
    now=datetime.now(timezone.utc).replace(second=0,microsecond=0)
    store.save('local',{'mode':'aws','account_id':account,'region':region,
        'resources':[{'resource_id':inst_id,'service':'ec2','state':'running'}],
        'telemetry':{'series':[{'resource_id':inst_id,'metric':'CPUUtilization','statistic':'Average','unit':'Percent','period_seconds':300,
            'points':[{'timestamp':(now-timedelta(minutes=5*m)).isoformat(),'value':0.5} for m in range(12,0,-1)]}]}})
    plan=idle_ec2_plan(store,account,region)[0]
    instance=ec2.describe_instances.return_value['Reservations'][0]['Instances'][0]
    instance['Tags'].append({'Key':'CostIntelIdleStop','Value':'true'})
    def wait_for(name):
        waiter=Mock()
        waiter.wait.side_effect=lambda **kw: instance['State'].update(Name='stopped' if name=='instance_stopped' else 'running')
        return waiter
    ec2.get_waiter.side_effect=wait_for
    # Stop instance
    res = apply_stop_instance(store, session, plan)
    assert res['status'] == 'applied'
    ec2.stop_instances.assert_called_once_with(InstanceIds=[inst_id])

    # Replay is idempotent
    res2 = apply_stop_instance(store, session, plan)
    assert res2['status'] == 'applied'
    assert ec2.stop_instances.call_count == 1

    # Restart / rollback
    restore_res = apply_start_instance(store, session, plan['id'])
    assert restore_res['status'] == 'restored'
    ec2.start_instances.assert_called_once_with(InstanceIds=[inst_id])

    # Verify audit history
    history = store.optimization_history('local', 'aws', account, region)
    assert len(history) == 1
    assert history[0]['status'] == 'restored'


def test_apply_stop_instance_opt_out(tmp_path):
    from costintel.actions import apply_stop_instance
    store = SnapshotStore(tmp_path / 'actions.db')
    account, region, inst_id = '123456789012', 'ap-south-1', 'i-0abcdef1234567890'
    session, sts, ec2 = Mock(), Mock(), Mock()
    session.client.side_effect = lambda name, **kw: sts if name == 'sts' else ec2
    sts.get_caller_identity.return_value = {'Account': account, 'Arn': f'arn:aws:iam::{account}:user/test'}
    ec2.describe_instances.return_value = {
        'Reservations': [{'Instances': [{
            'InstanceId': inst_id,
            'State': {'Name': 'running'},
            'Tags': [{'Key': 'CostIntelLab', 'Value': 'true'}, {'Key': 'AutoOptimize', 'Value': 'false'}]
        }]}]
    }
    plan = {'id': 'plan-stop-2', 'action': 'stop_idle_instance', 'account': account, 'region': region, 'resource': inst_id}
    with pytest.raises(ValueError, match='opt in'):
        apply_stop_instance(store, session, plan)
    ec2.stop_instances.assert_not_called()


def test_unused_volume_tagging_and_plans(tmp_path):
    from costintel.actions import unused_volume_plan, apply_tag_volume, generate_optimization_plans
    store = SnapshotStore(tmp_path / 'actions.db')
    account, region, vol_id = '123456789012', 'ap-south-1', 'vol-0123456789abcdef0'
    snap = {
        'mode': 'aws', 'account_id': account, 'region': region,
        'resources': [{'resource_id': vol_id, 'service': 'ebs', 'state': 'available', 'attachments': 0}],
    }
    store.save('local', snap)
    plans = unused_volume_plan(store, account, region)
    assert len(plans) == 1
    assert plans[0]['action'] == 'tag_unused_volume'
    assert plans[0]['resource'] == vol_id
    assert plans[0]['est_monthly_savings_usd'] is None

    # Test tagging
    session, sts, ec2 = Mock(), Mock(), Mock()
    session.client.side_effect = lambda name, **kw: sts if name == 'sts' else ec2
    sts.get_caller_identity.return_value = {'Account': account, 'Arn': f'arn:aws:iam::{account}:user/test'}
    ec2.describe_volumes.return_value = {
        'Volumes': [{'VolumeId': vol_id, 'State':'available', 'Attachments':[], 'Tags': [{'Key': 'CostIntelLab', 'Value': 'true'}, {'Key': 'AutoOptimize', 'Value': 'true'}]}]
    }
    ec2.create_tags.side_effect=lambda **kw: ec2.describe_volumes.return_value['Volumes'][0]['Tags'].extend(kw['Tags'])
    res = apply_tag_volume(store, session, plans[0])
    assert res['status'] == 'applied'
    ec2.create_tags.assert_called_once()

@pytest.mark.parametrize('case',['stale','gap','future','short','wrong_unit','nan'])
def test_idle_shutdown_rejects_unreliable_evidence(tmp_path,case):
    from costintel.actions import idle_ec2_plan
    store=SnapshotStore(tmp_path/'idle.db')
    now=datetime.now(timezone.utc).replace(second=0,microsecond=0)
    points=[{'timestamp':(now-timedelta(minutes=5*m)).isoformat(),'value':0.5} for m in range(12,0,-1)]
    unit='Percent'
    if case=='stale':
        for p in points:p['timestamp']=(datetime.fromisoformat(p['timestamp'])-timedelta(days=1)).isoformat()
    elif case=='future':
        for p in points:p['timestamp']=(datetime.fromisoformat(p['timestamp'])+timedelta(days=1)).isoformat()
    elif case=='gap':points[5]['timestamp']=(now-timedelta(minutes=200)).isoformat()
    elif case=='short':points=points[:3]
    elif case=='wrong_unit':unit='Count'
    elif case=='nan':points[-1]['value']=float('nan')
    data={'mode':'aws','account_id':'123456789012','region':'ap-south-1','resources':[{'service':'ec2','resource_id':'i-test','state':'running'}],
          'telemetry':{'series':[{'resource_id':'i-test','metric':'CPUUtilization','unit':unit,'statistic':'Average','period_seconds':300,'points':points}]}}
    # SQLite rejects NaN as a null metric, which is also fail-closed.
    if case=='nan':
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):store.save('local',data)
    else:
        store.save('local',data)
        assert idle_ec2_plan(store,'123456789012','ap-south-1',now=now)==[]
