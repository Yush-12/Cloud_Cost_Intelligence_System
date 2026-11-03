from datetime import datetime,timedelta,timezone
from unittest.mock import Mock,patch
import pytest
from costintel.store import SnapshotStore
from costintel.policies import containment_plan,apply_detected_containment
from costintel.web import create_app
from tests.web_helpers import connect,post

ACCOUNT='123456789012'
REGION='ap-south-1'
RESOURCE=f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:costintel-lab-workload'


def evidence_store(tmp_path,age=300,spike=500):
    store=SnapshotStore(tmp_path/'test.db');now=datetime.now(timezone.utc).replace(microsecond=0)
    values=[8+(i%7)*.2+(i%3)*.1 for i in range(36)]+[spike]
    points=[{'timestamp':(now-timedelta(seconds=age+300*(36-i))).isoformat(),'value':value} for i,value in enumerate(values)]
    store.save('local',{'mode':'aws','account_id':ACCOUNT,'region':REGION,'resources':[],
        'telemetry':{'series':[{'resource_id':RESOURCE,'metric':'Duration','statistic':'Average','unit':'Milliseconds','period_seconds':300,'points':points}]}})
    return store,now


def test_fresh_anomaly_generates_containment_plan(tmp_path):
    store,now=evidence_store(tmp_path)
    plan=containment_plan(store,ACCOUNT,REGION,now)
    assert plan['action']=='contain_lambda'
    assert plan['evidence']['baseline_points']==36
    assert plan['evidence']['sample_ms']==500
    assert plan['est_monthly_savings_usd'] is None
    assert 'blocks' in plan['effect'].lower() or 'blocking' in plan['effect'].lower()


@pytest.mark.parametrize('age,spike',[(3600,500),(-300,500),(300,50)])
def test_stale_future_or_small_spike_cannot_contain(tmp_path,age,spike):
    store,now=evidence_store(tmp_path,age,spike)
    assert containment_plan(store,ACCOUNT,REGION,now) is None
    assert containment_plan(store,ACCOUNT,'ap-south-2',now) is None


def test_cooldown_and_no_repeat_after_restore(tmp_path):
    store,now=evidence_store(tmp_path);plan=containment_plan(store,ACCOUNT,REGION,now)
    from costintel.containment import initialize
    import json
    initialize(store)
    with store.connect() as db:
        db.execute('INSERT INTO containment_actions VALUES (?,?,?,?)',(plan['id'],json.dumps({'account':ACCOUNT,'resource':RESOURCE,'previous':None}),'restored',now.isoformat()))
    assert containment_plan(store,ACCOUNT,REGION,now) is None
    sdk=Mock()
    assert apply_detected_containment(store,sdk,plan)['status']=='restored'
    sdk.client.assert_not_called()


def preview_client(tmp_path):
    store=SnapshotStore(tmp_path/'web.db');app=create_app(store,'local','aws',ACCOUNT,REGION)
    client=app.test_client();sdk=connect(client)
    plan={'id':'plan-1','account':ACCOUNT,'region':REGION,'resource':'i-test','action':'stop_idle_instance'}
    with client.session_transaction() as cookie:sid=cookie['session_id']
    import time
    scope=app.extensions['workspace_sessions'][sid]
    scope['preview']={'token':'preview-token','expires':time.monotonic()+300,'plans':{plan['id']:plan}}
    return client,scope,sdk,plan


def test_execute_uses_server_plan_once(tmp_path):
    client,scope,sdk,plan=preview_client(tmp_path)
    with patch('costintel.actions.apply_optimization_plan',return_value={'id':plan['id'],'status':'applied'}) as apply:
        response=post(client,'/api/plans/execute',{'preview_token':'preview-token','plan_id':plan['id']})
        assert response.status_code==200
        assert apply.call_args.args[1] is sdk
        assert apply.call_args.args[2]==plan
        assert post(client,'/api/plans/execute',{'preview_token':'preview-token','plan_id':plan['id']}).status_code==409
        assert apply.call_count==1


@pytest.mark.parametrize('case',['expired','token','scope','payload'])
def test_invalid_selection_never_calls_aws(tmp_path,case):
    client,scope,sdk,plan=preview_client(tmp_path)
    payload={'preview_token':'preview-token','plan_id':plan['id']}
    expected=409
    if case=='expired':scope['preview']['expires']=0
    if case=='token':payload['preview_token']='wrong'
    if case=='scope':plan['account']='999999999999';expected=403
    if case=='payload':payload['plan']={'resource':'other'};expected=400
    with patch('costintel.actions.apply_optimization_plan') as apply:
        assert post(client,'/api/plans/execute',payload).status_code==expected
        apply.assert_not_called()


def test_uncertain_result_cannot_be_replayed(tmp_path):
    client,scope,sdk,plan=preview_client(tmp_path)
    payload={'preview_token':'preview-token','plan_id':plan['id']}
    with patch('costintel.actions.apply_optimization_plan',side_effect=RuntimeError('lost response')) as apply:
        assert post(client,'/api/plans/execute',payload).status_code==502
        assert post(client,'/api/plans/execute',payload).status_code==409
        assert apply.call_count==1


def test_demo_cannot_execute_real_plan(tmp_path):
    client,scope,sdk,plan=preview_client(tmp_path);connect(client,mode='demo')
    with patch('costintel.actions.apply_optimization_plan') as apply:
        assert post(client,'/api/plans/execute',{'preview_token':'preview-token','plan_id':plan['id']}).status_code==403
        apply.assert_not_called()


@pytest.mark.parametrize('previous', [None, 2])
def test_detect_apply_audit_and_exact_restore(tmp_path, previous):
    from tests.test_containment import setup
    from costintel.containment import restore
    store, now = evidence_store(tmp_path)
    _, client, state = setup(tmp_path, previous)
    plan = containment_plan(store, ACCOUNT, REGION, now)
    with patch('costintel.containment.client_for', return_value=client):
        result = apply_detected_containment(store, Mock(), plan)
    assert result['status'] == 'contained'
    assert state[0] == 0
    audit = store.containment_history('local', 'aws', ACCOUNT, REGION)[0]
    assert audit['evidence'] == plan['evidence']
    assert audit['previous'] == previous
    assert restore(store, client, ACCOUNT, plan['id'])['status'] == 'restored'
    assert state[0] == previous
