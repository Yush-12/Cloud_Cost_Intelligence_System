"""The paid Cost Explorer API is opt-in and bounded to one attempt per day."""
from datetime import date
from unittest.mock import Mock, patch

from costintel.billing import fetch_daily_costs
from costintel.store import SnapshotStore
from costintel.web import create_app
from tests.web_helpers import connect, post


def test_fetch_filters_verified_account_and_stops_after_one_page():
    sdk = Mock()
    ce = sdk.client.return_value
    ce.get_cost_and_usage.return_value = {
        'ResultsByTime': [{'TimePeriod': {'Start': '2026-11-03', 'End': '2026-11-04'},
                           'Total': {'UnblendedCost': {'Amount': '0.012345', 'Unit': 'USD'}},
                           'Estimated': False}],
        'NextPageToken': 'another-page'}
    data = fetch_daily_costs(sdk, '123456789012', today=date(2026, 11, 4), days=1)
    sdk.client.assert_called_once()
    assert sdk.client.call_args.args == ('ce',)
    assert sdk.client.call_args.kwargs['region_name'] == 'us-east-1'
    ce.get_cost_and_usage.assert_called_once_with(
        TimePeriod={'Start': '2026-11-03', 'End': '2026-11-04'},
        Granularity='DAILY', Metrics=['UnblendedCost'],
        Filter={'Dimensions': {'Key': 'LINKED_ACCOUNT', 'Values': ['123456789012']}})
    assert data['days'][0]['usd'] == '0.012345'
    assert data['truncated'] is True


def test_billing_api_is_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv('COSTINTEL_ENABLE_BILLING', raising=False)
    store = SnapshotStore(tmp_path/'billing-disabled.db')
    client = create_app(store, 'local', 'aws', '123456789012', 'ap-south-1').test_client()
    connect(client)
    assert b'Request billing history' not in client.get('/').data
    with patch('costintel.billing.fetch_daily_costs') as fetch:
        response = post(client, '/api/billing/sync', {'confirm_charge': True})
    assert response.status_code == 403
    fetch.assert_not_called()
    assert store.billing_latest('local', '123456789012') is None


def test_billing_requires_confirmation_and_allows_one_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv('COSTINTEL_ENABLE_BILLING', '1')
    store = SnapshotStore(tmp_path/'billing-enabled.db')
    client = create_app(store, 'local', 'aws', '123456789012', 'ap-south-1').test_client()
    connect(client)
    assert b'Request billing history' in client.get('/').data
    data = {'source': 'AWS Cost Explorer', 'scope': 'account-wide', 'days': [], 'truncated': False}
    with patch('costintel.billing.fetch_daily_costs', return_value=data) as fetch:
        assert post(client, '/api/billing/sync', {}).status_code == 400
        assert post(client, '/api/billing/sync', {'confirm_charge': True}).status_code == 200
        duplicate = post(client, '/api/billing/sync', {'confirm_charge': True})
    assert duplicate.status_code == 409
    assert fetch.call_count == 1
    assert store.billing_latest('local', '123456789012')['payload'] == data
    assert store.billing_latest('other', '123456789012') is None
    assert store.billing_latest('local', '999999999999') is None


def test_failed_billing_attempt_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setenv('COSTINTEL_ENABLE_BILLING', '1')
    store = SnapshotStore(tmp_path/'billing-failed.db')
    client = create_app(store, 'local', 'aws', '123456789012', 'ap-south-1').test_client()
    connect(client)
    with patch('costintel.billing.fetch_daily_costs', side_effect=RuntimeError('network')) as fetch:
        assert post(client, '/api/billing/sync', {'confirm_charge': True}).status_code == 502
        assert post(client, '/api/billing/sync', {'confirm_charge': True}).status_code == 409
    assert fetch.call_count == 1
    assert store.billing_latest('local', '123456789012')['status'] == 'failed'
