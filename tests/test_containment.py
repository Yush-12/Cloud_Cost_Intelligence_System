import json
from unittest.mock import Mock

import pytest

from costintel.containment import apply, restore
from costintel.store import SnapshotStore

ACCOUNT = '123456789012'
ARN = f'arn:aws:lambda:ap-south-1:{ACCOUNT}:function:costintel-lab-workload'


def setup(tmp_path, previous=None):
    store, client = SnapshotStore(tmp_path / 'actions.db'), Mock()
    client.get_function.return_value = {'Configuration': {'FunctionArn': ARN, 'State': 'Active',
        'LastUpdateStatus': 'Successful', 'MemorySize': 128, 'Timeout': 3}, 'Tags': {'CostIntelLab': 'true', 'CostIntelContainment': 'true'}}
    state = [previous]
    client.get_function_concurrency.side_effect = lambda **kw: ({} if state[0] is None else {'ReservedConcurrentExecutions': state[0]})
    def put(**kw):
        state[0] = kw['ReservedConcurrentExecutions']
    client.put_function_concurrency.side_effect = put
    client.delete_function_concurrency.side_effect = lambda **kw: state.__setitem__(0, None)
    return store, client, state


@pytest.mark.parametrize('previous', [None, 2])
def test_exact_restore_and_repeat_restore(tmp_path, previous):
    store, client, state = setup(tmp_path, previous)
    result = apply(store, client, ACCOUNT)
    assert state[0] == 0
    assert restore(store, client, ACCOUNT, result['id'])['status'] == 'restored'
    assert state[0] == previous
    assert store.containment_history('local', 'aws', ACCOUNT, 'ap-south-1')[0]['status'] == 'restored'
    assert store.containment_history('other', 'aws', ACCOUNT, 'ap-south-1') == []
    assert store.containment_history('local', 'aws', 'other', 'ap-south-1') == []
    assert store.containment_history('local', 'aws', ACCOUNT, 'us-east-1') == []
    calls = client.put_function_concurrency.call_count + client.delete_function_concurrency.call_count
    restore(store, client, ACCOUNT, result['id'])
    assert calls == client.put_function_concurrency.call_count + client.delete_function_concurrency.call_count


def test_opt_out_does_not_mutate(tmp_path):
    store, client, _ = setup(tmp_path)
    client.get_function.return_value['Tags']['CostIntelContainment'] = 'false'
    with pytest.raises(ValueError):
        apply(store, client, ACCOUNT)
    client.put_function_concurrency.assert_not_called()


def test_unknown_outcome_keeps_before_state_and_blocks_repeat(tmp_path):
    store, client, state = setup(tmp_path)
    def uncertain(**kw):
        state[0] = 0
        raise RuntimeError('lost response')
    client.put_function_concurrency.side_effect = uncertain
    with pytest.raises(RuntimeError):
        apply(store, client, ACCOUNT)
    with store.connect() as db:
        row = db.execute('SELECT id, plan, status FROM containment_actions').fetchone()
    assert row[2] == 'uncertain'
    assert json.loads(row[1])['previous'] is None
    with pytest.raises(ValueError):
        apply(store, client, ACCOUNT)
    assert client.put_function_concurrency.call_count == 1
    assert restore(store, client, ACCOUNT, row[0])['status'] == 'restored'


def test_external_change_and_wrong_account_block_restore(tmp_path):
    store, client, state = setup(tmp_path)
    action = apply(store, client, ACCOUNT)
    state[0] = 7
    with pytest.raises(ValueError, match='externally'):
        restore(store, client, ACCOUNT, action['id'])
    with pytest.raises(ValueError, match='account'):
        restore(store, client, 'other', action['id'])
    client.delete_function_concurrency.assert_not_called()


def test_demo_proves_before_blocked_restored(tmp_path):
    import io
    from botocore.exceptions import ClientError
    from scripts.verify_containment import demonstrate
    store, client, state = setup(tmp_path)
    def invoke(**kw):
        if state[0] == 0:
            raise ClientError({'Error': {'Code': 'TooManyRequestsException'}}, 'Invoke')
        return {'StatusCode': 200, 'Payload': io.BytesIO(b'{}')}
    client.invoke.side_effect = invoke
    journal = tmp_path / 'demo.jsonl'
    demonstrate(store, client, ACCOUNT, journal)
    assert state[0] is None
    assert client.invoke.call_count == 3
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    assert [e['result'] for e in events if 'result' in e] == ['executed', 'throttled', 'executed']
    assert events[-1]['event'] == 'finished'


def test_demo_restores_after_unknown_apply_response(tmp_path):
    import io
    from scripts.verify_containment import demonstrate
    store, client, state = setup(tmp_path)
    client.invoke.return_value = {'StatusCode': 200, 'Payload': io.BytesIO(b'{}')}
    def uncertain(**kw):
        state[0] = 0
        raise RuntimeError('lost response')
    client.put_function_concurrency.side_effect = uncertain
    with pytest.raises(RuntimeError):
        demonstrate(store, client, ACCOUNT, tmp_path / 'demo.jsonl')
    assert state[0] is None
    assert client.invoke.call_count == 1
