"""Opt-in, bounded Cost Explorer retrieval; never called by normal collection."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from botocore.config import Config


def fetch_daily_costs(session, account_id, *, today=None, days=30):
    if not 1 <= days <= 31:
        raise ValueError('Billing history must cover 1 to 31 completed days')
    if not isinstance(account_id, str) or len(account_id) != 12 or not account_id.isdecimal():
        raise ValueError('A verified AWS account ID is required')
    today = today or datetime.now(timezone.utc).date()
    start = today - timedelta(days=days)
    client = session.client('ce', region_name='us-east-1', config=Config(
        connect_timeout=5, read_timeout=15, retries={'total_max_attempts': 1}))
    response = client.get_cost_and_usage(
        TimePeriod={'Start': start.isoformat(), 'End': today.isoformat()},
        Granularity='DAILY', Metrics=['UnblendedCost'],
        Filter={'Dimensions': {'Key': 'LINKED_ACCOUNT', 'Values': [account_id]}})
    rows = []
    for period in response.get('ResultsByTime', []):
        cost = period['Total']['UnblendedCost']
        if cost['Unit'] != 'USD':
            raise ValueError('Unexpected Cost Explorer currency')
        try:
            amount = Decimal(cost['Amount'])
        except (InvalidOperation, TypeError) as exc:
            raise ValueError('Invalid Cost Explorer amount') from exc
        if not amount.is_finite():
            raise ValueError('Invalid Cost Explorer amount')
        rows.append({'date': period['TimePeriod']['Start'], 'usd': str(amount),
                     'estimated': bool(period.get('Estimated', False))})
    return {'source': 'AWS Cost Explorer', 'metric': 'UnblendedCost',
            'scope': 'account-wide', 'currency': 'USD', 'start': start.isoformat(),
            'end_exclusive': today.isoformat(), 'fetched_at': datetime.now(timezone.utc).isoformat(),
            'days': rows, 'truncated': bool(response.get('NextPageToken'))}
