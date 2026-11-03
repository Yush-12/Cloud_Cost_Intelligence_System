"""Offline authentication helpers; all AWS identity calls are mocked."""
from unittest.mock import Mock, patch


def post(client, url, body=None, **kwargs):
    token=client.get('/api/session').json['csrf_token']
    return client.post(url, json=body or {}, headers={'X-CSRF-Token':token}, **kwargs)


def connect(client, account='123456789012', mode='aws'):
    if mode=='demo':
        assert post(client,'/api/auth/login',{'mode':'demo'}).status_code==200
        return
    sdk=Mock()
    sdk.client.return_value.get_caller_identity.return_value={'Account':account,'Arn':f'arn:aws:iam::{account}:user/test'}
    with patch('costintel.web.boto3.Session',return_value=sdk), patch('costintel.web.collect_aws',side_effect=RuntimeError('offline')):
        assert post(client,'/api/auth/login',{'access_key_id':'ASIATEST','secret_access_key':'example-secret','session_token':'example-token','region':'ap-south-1'}).status_code==200
    return sdk
