"""Hosted surface stays account-scoped and read-only until durable actions exist."""
from unittest.mock import Mock, patch
from contextlib import contextmanager
import sqlite3
import time

from costintel.store import SnapshotStore
from costintel.web import create_app
from tests.web_helpers import post


SECRET = 'a-stable-random-secret-with-more-than-32-characters'


def test_vercel_without_durable_configuration_fails_closed():
    from api.index import create_vercel_app
    response=create_vercel_app({}).test_client().get('/login')
    assert response.status_code==503
    assert b'Hosted AWS workspace setup is incomplete' in response.data
    assert b'Explore the offline demo' not in response.data


def hosted_client(tmp_path):
    store=SnapshotStore(tmp_path/'hosted-test.db')
    app=create_app(store,'hosted','aws','','ap-south-1',deployment='hosted',
                   session_registry={},session_secret=SECRET)
    return app.test_client(),store


def test_hosted_login_page_offers_demo_and_aws(tmp_path):
    client,_=hosted_client(tmp_path)
    page=client.get('/login')
    assert page.status_code==200
    assert b'Explore the offline demo' in page.data
    assert b'HOSTED WORKSPACE' in page.data
    assert b'LOCAL WORKSPACE' not in page.data
    assert b'encrypted in the hosted database' in page.data
    assert b'Temporary AWS credentials' in page.data or b'temporary AWS credentials' in page.data
    assert post(client,'/api/auth/login',{'mode':'demo','access_key_id':'ASIATEST'}).status_code==400


def test_hosted_demo_is_stateless_synthetic_and_never_calls_aws(tmp_path, monkeypatch):
    client, store = hosted_client(tmp_path)
    with patch('costintel.web.boto3.Session', side_effect=AssertionError('AWS session created')), \
         patch('costintel.web.collect_aws', side_effect=AssertionError('AWS inventory called')):
        assert post(client,'/api/auth/login',{'mode':'demo'}).status_code==200
        assert client.get('/api/session').json['mode'] == 'demo'
        dashboard = client.get('/')
        assert dashboard.status_code == 200
        assert b'SIMULATED DEMO' in dashboard.data
        assert b'Example: detect, contain, restore.' in dashboard.data
        assert b'demo-containment-001' in dashboard.data
        assert b'No AWS account is connected' in dashboard.data
        inventory = client.get('/api/inventory').json
        assert inventory['mode'] == 'demo'
        assert inventory['snapshot']['telemetry']['synthetic'] is True
        assert inventory['snapshot']['telemetry']['api_calls'] == 0
        assert len(inventory['snapshot']['resources']) == 4
        detection = client.get('/api/detection').json['results']
        assert any(row['status'] == 'outlier' for row in detection)
        assert client.get('/api/metrics?resource_id=demo-lambda-workload&metric=Duration').json['points']
        cycle = post(client,'/api/pipeline/run',{'apply':False,'window_hours':6}).json
        assert cycle['report']['telemetry']['api_calls'] == 0
        assert cycle['report']['plans_generated'] == []
        assert store.latest('hosted','demo','demo-account','ap-south-1') is None
        assert post(client,'/api/plans/execute',{'preview_token':'x','plan_id':'x'}).status_code == 403
        assert post(client,'/api/workload/restore',{'action_id':'x'}).status_code == 403
        assert post(client,'/api/auth/logout').status_code == 200
        assert client.get('/api/session').json['authenticated'] is False


def test_hosted_demo_cookie_survives_a_new_instance(tmp_path):
    first, _ = hosted_client(tmp_path)
    assert post(first,'/api/auth/login',{'mode':'demo'}).status_code == 200
    cookie = first.get_cookie('session')
    second, _ = hosted_client(tmp_path)
    second.set_cookie('session', cookie.value)
    assert second.get('/').status_code == 200
    assert second.get('/api/session').json['authenticated'] is True


def test_switching_from_aws_to_demo_deletes_aws_session(tmp_path):
    client, _ = hosted_client(tmp_path)
    sdk = Mock()
    sdk.client.return_value.get_caller_identity.return_value = {
        'Account':'123456789012', 'Arn':'arn:aws:iam::123456789012:user/reader'}
    snapshot = {'mode':'aws', 'account_id':'123456789012', 'region':'ap-south-1',
                'collected_at':'2026-11-03T00:00:00+00:00', 'resources':[]}
    with patch('costintel.web.boto3.Session', return_value=sdk), \
         patch('costintel.web.collect_aws', return_value=snapshot):
        assert post(client,'/api/auth/login',{'mode':'aws','region':'ap-south-1',
            'access_key_id':'ASIATEST','secret_access_key':'example-secret',
            'session_token':'example-token'}).status_code == 200
    assert client.get('/api/session').json['mode'] == 'aws'
    assert post(client,'/api/auth/login',{'mode':'demo'}).status_code == 200
    assert client.get('/api/session').json['mode'] == 'demo'
    assert client.get('/api/inventory').json['mode'] == 'demo'
    assert client.application.extensions['workspace_sessions'] == {}


def test_hosted_connection_is_scoped_and_never_generates_action_plans(tmp_path):
    client,store=hosted_client(tmp_path)
    sdk=Mock()
    sdk.client.return_value.get_caller_identity.return_value={
        'Account':'123456789012','Arn':'arn:aws:iam::123456789012:user/reader'}
    snapshot={'mode':'aws','account_id':'123456789012','region':'ap-south-1',
              'collected_at':'2026-11-03T00:00:00+00:00','resources':[],
              'telemetry':{'series':[]}}
    with patch('costintel.web.boto3.Session',return_value=sdk), patch('costintel.web.collect_aws',return_value=snapshot):
        response=post(client,'/api/auth/login',{'mode':'aws','region':'ap-south-1',
            'access_key_id':'ASIATEST','secret_access_key':'example-secret','session_token':'example-token'})
    assert response.status_code==200
    dashboard=client.get('/')
    assert dashboard.status_code==200
    assert b'Hosted AWS Workspace' in dashboard.data
    assert b'Collect &amp; Analyze' in dashboard.data
    assert store.latest('hosted','aws','123456789012','ap-south-1') is None
    report={'mode':'aws','account':'123456789012','region':'ap-south-1','snapshot_id':'x','timestamp':'now',
            'inventory':{'resources_count':0,'services':[]},'telemetry':{'series_collected':0,'api_calls':0},
            'detection':{'total_analyzed':0,'outliers_count':0,'outliers':[],
                         'insufficient_variation_count':0,'insufficient_data_count':0},
            'plans_generated':[],'execution':{'mode':'dry_run','results':[]}}
    with patch('costintel.pipeline.run_pipeline',return_value=report) as run:
        assert post(client,'/api/pipeline/run').status_code==200
        assert run.call_args.kwargs['action_type']=='observe'
        assert run.call_args.kwargs['owner']!='local'
    assert post(client,'/api/plans/execute',{'preview_token':'x','plan_id':'x'}).status_code==403
    assert post(client,'/api/workload/restore',{'action_id':'x'}).status_code==403
    logout=post(client,'/api/auth/logout')
    assert logout.status_code==200
    assert b'Encrypted temporary credentials deleted' in logout.data
    assert client.get('/api/inventory').status_code==401
    assert client.get('/api/session').json['authenticated'] is False
    assert client.get('/').headers['Location'].endswith('/login')
    assert post(client,'/api/auth/logout').status_code==200


def test_hosted_sessions_encrypt_credentials_and_survive_new_instance(tmp_path):
    from costintel.hosted_sessions import HostedSessions

    class SqliteConnection:
        def __init__(self, path):
            self.path=path

        @contextmanager
        def connect(self):
            with sqlite3.connect(self.path) as db:
                class Adapter:
                    def execute(self, sql, params=()):
                        return db.execute(sql.replace('%s','?'),params)
                yield Adapter()

    database=SqliteConnection(tmp_path/'sessions.db')
    first=HostedSessions(database,SECRET)
    first['session-1']={'expires':time.monotonic()+3600,'owner':'owner-1','mode':'aws',
        'account_id':'123456789012','region':'ap-south-1',
        'credentials':{'aws_access_key_id':'ASIATEST','aws_secret_access_key':'private-test-secret',
                       'aws_session_token':'private-test-token'}}
    with sqlite3.connect(database.path) as db:
        stored=db.execute('SELECT payload FROM hosted_sessions WHERE sid=?',('session-1',)).fetchone()[0]
    assert b'private-test-secret' not in stored
    second=HostedSessions(database,SECRET)
    scope=second['session-1']
    assert scope['account_id']=='123456789012'
    assert scope['sdk'].region_name=='ap-south-1'
    assert scope['credentials']['aws_session_token']=='private-test-token'
    second.pop('session-1')
    assert 'session-1' not in first

    first['expired-on-lookup']={'expires':time.monotonic()+3600,'owner':'owner-1','mode':'aws',
        'account_id':'123456789012','region':'ap-south-1',
        'credentials':{'aws_access_key_id':'ASIATEST','aws_secret_access_key':'private-test-secret',
                       'aws_session_token':'private-test-token'}}
    with sqlite3.connect(database.path) as db:
        db.execute('UPDATE hosted_sessions SET expires_at=? WHERE sid=?', (time.time()-1, 'expired-on-lookup'))
    assert first.get('expired-on-lookup') is None
    with sqlite3.connect(database.path) as db:
        assert db.execute('SELECT 1 FROM hosted_sessions WHERE sid=?', ('expired-on-lookup',)).fetchone() is None

    first['expired-on-start']={'expires':time.monotonic()+3600,'owner':'owner-1','mode':'aws',
        'account_id':'123456789012','region':'ap-south-1',
        'credentials':{'aws_access_key_id':'ASIATEST','aws_secret_access_key':'private-test-secret',
                       'aws_session_token':'private-test-token'}}
    with sqlite3.connect(database.path) as db:
        db.execute('UPDATE hosted_sessions SET expires_at=? WHERE sid=?', (time.time()-1, 'expired-on-start'))
    HostedSessions(database,SECRET)
    with sqlite3.connect(database.path) as db:
        assert db.execute('SELECT 1 FROM hosted_sessions WHERE sid=?', ('expired-on-start',)).fetchone() is None
