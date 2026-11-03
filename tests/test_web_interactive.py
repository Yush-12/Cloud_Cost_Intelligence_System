import json
from unittest.mock import patch
import pytest
from costintel.store import SnapshotStore
from costintel.web import create_app
from tests.web_helpers import connect, post

@pytest.fixture
def app(tmp_path):
    return create_app(SnapshotStore(tmp_path/'test.db'),'local','aws','123456789012','ap-south-1')

def test_no_unauthenticated_fallback(app):
    client=app.test_client()
    assert client.get('/').status_code==302
    for path in ('inventory','actions','detection','profiles','metrics'):
        assert client.get('/api/'+path).status_code==401
    assert post(client,'/api/pipeline/run').status_code==401

def test_csrf_and_origin(app):
    client=app.test_client()
    assert client.post('/api/auth/login',json={'mode':'demo'}).status_code==403
    token=client.get('/api/session').json['csrf_token']
    assert client.post('/api/auth/login',json={'mode':'demo'},headers={'X-CSRF-Token':token,'Origin':'https://other.example'}).status_code==403
    assert client.post('/api/auth/login',json=[],headers={'X-CSRF-Token':token}).status_code==400

def test_login_logout_and_expiry(app):
    client=app.test_client();connect(client,mode='demo')
    assert client.get('/').status_code==200
    assert post(client,'/api/auth/logout').status_code==200
    assert client.get('/api/inventory').status_code==401
    connect(client,mode='demo')
    for value in app.extensions['workspace_sessions'].values(): value['expires']=0
    assert client.get('/api/inventory').status_code==401

def test_session_isolation(app):
    a,b=app.test_client(),app.test_client()
    connect(a,'111122223333');connect(b,'444455556666')
    assert a.get('/api/inventory').json['account_id']=='111122223333'
    assert b.get('/api/inventory').json['account_id']=='444455556666'
    connect(b,mode='demo')
    assert a.get('/api/inventory').json['mode']=='aws'
    assert post(b,'/api/profiles/switch',{'profile':'costintel'}).status_code==409


def test_temporary_credentials_verify_identity_with_regional_sts(app):
    from unittest.mock import Mock
    client=app.test_client()
    sdk=Mock()
    sdk.client.return_value.get_caller_identity.return_value={
        'Account':'123456789012','Arn':'arn:aws:iam::123456789012:user/test'}
    with patch('costintel.web.boto3.Session',return_value=sdk), patch('costintel.web.collect_aws',side_effect=RuntimeError('offline')):
        response=post(client,'/api/auth/login',{'region':'ap-south-1','access_key_id':'ASIATEST',
            'secret_access_key':'example-secret','session_token':'example-token'})
    assert response.status_code==200
    assert sdk.client.call_args.kwargs['endpoint_url']=='https://sts.ap-south-1.amazonaws.com'


def test_local_website_rejects_profile_login(app):
    client=app.test_client()
    with patch('costintel.web.boto3.Session') as sdk:
        assert post(client,'/api/auth/login',{'profile':'costintel','region':'ap-south-1'}).status_code==400
        sdk.assert_not_called()

def test_preview_uses_connected_sdk(app):
    client=app.test_client();sdk=connect(client)
    report={'inventory':{'resources_count':0,'services':[]},'telemetry':{'series_collected':0,'api_calls':0},'detection':{'total_analyzed':0,'outliers_count':0,'outliers':[],'insufficient_variation_count':0,'insufficient_data_count':0},'execution':{'mode':'dry_run','results':[]},'plans_generated':[],'mode':'aws','account':'123456789012','region':'ap-south-1','snapshot_id':1,'timestamp':'now'}
    with patch('costintel.pipeline.run_pipeline',return_value=report) as run:
        assert post(client,'/api/pipeline/run').status_code==200
        assert run.call_args.kwargs['session'] is sdk
        assert run.call_args.kwargs['apply'] is False
    for payload,status in [({'apply':True},409),({'apply':'false'},400),({'profile':'other'},400),({'window_hours':49},400),({'stimulate':'slow'},409)]:
        assert post(client,'/api/pipeline/run',payload).status_code==status

def test_unsafe_operations_disabled(app):
    c=app.test_client();connect(c,mode='demo')
    for path in ('lab/provision','lab/teardown','workload/stimulate'):
        assert post(c,'/api/'+path).status_code==404
    assert post(c,'/api/workload/restore').status_code==400
    assert post(c,'/api/workload/restore',{'action_id':'x'}).status_code==403

def test_restore_scope_and_failure_preserves_audit(tmp_path):
    store=SnapshotStore(tmp_path/'restore.db')
    with store.connect() as db:
        db.execute('CREATE TABLE containment_actions (id TEXT PRIMARY KEY,plan TEXT,status TEXT,created_at TEXT)')
        for key,account,region in [('own','123456789012','ap-south-1'),('other','999999999999','ap-south-1'),('region','123456789012','us-east-1')]:
            db.execute('INSERT INTO containment_actions VALUES (?,?,?,?)',(key,json.dumps({'account':account,'region':region}),'contained','2026-11-01T00:00:00Z'))
    c=create_app(store,'local','aws','123456789012','ap-south-1').test_client();connect(c)
    with patch('costintel.containment.client_for',return_value=object()),patch('costintel.containment.restore',side_effect=RuntimeError('offline')) as restore:
        for key in ('other','region'):
            assert post(c,'/api/workload/restore',{'action_id':key}).status_code==403
        restore.assert_not_called()
        assert post(c,'/api/workload/restore',{'action_id':'own'}).status_code==500
    with store.connect() as db:
        assert all(row[0]=='contained' for row in db.execute('SELECT status FROM containment_actions'))

def test_public_deployment_rejects_aws(tmp_path):
    c=create_app(SnapshotStore(tmp_path/'public.db'),'local','demo','demo-account','ap-south-1',deployment='demo').test_client()
    with patch('costintel.web.boto3.Session') as aws:
        assert post(c,'/api/auth/login',{'profile':'costintel'}).status_code==403
        aws.assert_not_called()
    connect(c,mode='demo')
    assert c.get('/').status_code==200
    assert b'data-preset="costintel"' not in c.get('/login').data


def test_login_page_contains_current_controls(app):
    page=app.test_client().get('/login')
    assert page.status_code==200
    assert b'Explore the offline demo' in page.data
    assert b'temporary AWS credentials' in page.data
    assert b'Your AWS CLI profile' not in page.data
    assert b'csrf-token' in page.data
