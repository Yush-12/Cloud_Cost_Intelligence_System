import json
from unittest.mock import Mock

import pytest

from scripts.baseline_experiment import execute, plan


def test_plan_has_exact_cap_and_separate_buckets():
    slots = plan(1000)
    assert len(slots) == 37
    assert sum(s['count'] for s in slots) == 111
    assert all(s['scenario'] == 'baseline' for s in slots[:-1])
    assert slots[-1]['scenario'] == 'slow'
    assert all(b['at'] - a['at'] == 300 for a, b in zip(slots, slots[1:]))


def test_success_and_existing_journal_cannot_replay(tmp_path):
    now = [1000.0]
    def sleep(seconds):
        now[0] += seconds
    invoke = Mock(return_value={'invocations': 3})
    journal = tmp_path / 'run.jsonl'
    execute(None, 'account', journal, lambda: now[0], sleep, invoke)
    assert invoke.call_count == 37
    assert json.loads(journal.read_text().splitlines()[-1])['event'] == 'finished'
    with pytest.raises(FileExistsError):
        execute(None, 'account', journal, lambda: now[0], sleep, invoke)
    assert invoke.call_count == 37


def test_uncertain_result_stops_without_retry(tmp_path):
    now = [1000.0]
    def sleep(seconds):
        now[0] += seconds
    invoke = Mock(side_effect=RuntimeError('uncertain'))
    journal = tmp_path / 'run.jsonl'
    with pytest.raises(RuntimeError):
        execute(None, 'account', journal, lambda: now[0], sleep, invoke)
    assert invoke.call_count == 1
    events = [json.loads(line)['event'] for line in journal.read_text().splitlines()]
    assert events == ['planned', 'attempting', 'stopped']


def test_sleeping_computer_aborts_without_catchup(tmp_path):
    now = [1000.0]
    def sleep(seconds):
        now[0] += 1000
    invoke = Mock()
    with pytest.raises(ValueError, match='Missed'):
        execute(None, 'account', tmp_path / 'run.jsonl', lambda: now[0], sleep, invoke)
    invoke.assert_not_called()
