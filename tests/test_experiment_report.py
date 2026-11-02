import json
from unittest.mock import Mock

from costintel.experiment import report


def test_report_distinguishes_completed_uncertain_and_missing(tmp_path):
    path = tmp_path / 'run.jsonl'
    events = [{'event': 'planned', 'account': '123456789012', 'region': 'ap-south-1',
               'schedule': [{'index': i, 'at': 300*i+30, 'count': 3, 'scenario': 'baseline'} for i in range(3)]},
              {'event': 'attempting', 'index': 0},
              {'event': 'completed', 'index': 0, 'result': {'invocations': 3}},
              {'event': 'attempting', 'index': 1}, {'event': 'stopped'}]
    path.write_text('\n'.join(json.dumps(e) for e in events))
    store = Mock()
    store.history.return_value = [{'timestamp': '1970-01-01T00:00:00+00:00',
                                   'value': 3, 'statistic': 'Sum', 'period_seconds': 300, 'unit': 'Count'}]
    result = report(path, store)
    assert result['state'] == 'stopped'
    assert result['confirmed_journal_calls'] == 3
    assert result['unconfirmed_attempts'] == [1]
    assert result['buckets'][0]['matches_plan'] is True
    assert result['buckets'][1]['matches_plan'] is None
    assert result['observed_buckets'] == 1
    assert store.history.call_args.args[:4] == ('local', 'aws', '123456789012', 'ap-south-1')
