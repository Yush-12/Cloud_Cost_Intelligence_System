"""Plan or run a bounded lab experiment. Default is an offline preview."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

import boto3

from scripts.run_lambda_lab import run, verify_lab


def plan(start):
    # Begin 30 seconds into the next complete five-minute bucket.
    first = (int(start) // 300 + 1) * 300 + 30
    return [{'index': i, 'at': first + i * 300, 'count': 3,
             'scenario': 'baseline' if i < 36 else 'slow'} for i in range(37)]


def append(path, event):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(event) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def execute(session, account, journal, clock=time.time, sleep=time.sleep, invoke=run):
    journal = Path(journal)
    journal.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents two workers and prevents accidental replay.
    # Interrupted journals require inspection; restarting uses a new experiment.
    with journal.open('x', encoding='utf-8') as stream:
        schedule = plan(clock())
        stream.write(json.dumps({'event': 'planned', 'account': account,
                                 'region': 'ap-south-1', 'schedule': schedule}) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    try:
        for slot in schedule:
            while clock() < slot['at']:
                sleep(min(30, slot['at'] - clock()))
            if clock() - slot['at'] > 60:
                raise ValueError('Missed scheduled bucket; stop instead of catching up')
            append(journal, {'event': 'attempting', **slot})
            result = invoke(session, account, count=slot['count'], scenario=slot['scenario'])
            append(journal, {'event': 'completed', 'index': slot['index'], 'result': result})
            print(f"Completed bucket {slot['index'] + 1}/37 ({slot['scenario']})", flush=True)
    except BaseException:
        append(journal, {'event': 'stopped', 'at': clock(),
                         'message': 'Inspect last attempt; never replay uncertain invocations.'})
        raise
    append(journal, {'event': 'finished', 'at': clock()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--profile')
    parser.add_argument('--expected-account')
    parser.add_argument('--journal', default='.local-data/experiments/baseline.jsonl')
    args = parser.parse_args()
    if not args.execute:
        schedule = plan(time.time())
        print(json.dumps({'buckets': 37, 'baseline_invocations': 108,
                          'slow_invocations': 3, 'total_invocations': 111,
                          'first_utc': datetime.fromtimestamp(schedule[0]['at'], timezone.utc).isoformat(),
                          'last_utc': datetime.fromtimestamp(schedule[-1]['at'], timezone.utc).isoformat(),
                          'region': 'ap-south-1', 'journal': args.journal}, indent=2))
        return
    if not args.profile or not args.expected_account:
        parser.error('--execute requires --profile and --expected-account')
    if Path(args.journal).exists():
        parser.error('Journal already exists; inspect it instead of replaying the run')
    session = boto3.Session(profile_name=args.profile)
    # Fail before scheduling when authentication or lab configuration is invalid.
    verify_lab(session, args.expected_account)
    execute(session, args.expected_account, args.journal)


if __name__ == '__main__':
    main()
