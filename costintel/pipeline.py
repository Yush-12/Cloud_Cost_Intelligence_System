"""Unified End-to-End Cloud Cost Intelligence Pipeline.

Executes a complete cycle:
1. Collects live inventory and CloudWatch telemetry
2. Persists durable scoped time-series snapshots
3. Evaluates runtime anomaly detection via Isolation Forest
4. Generates policy-driven optimization plans (idle compute, runaway functions, unused storage)
5. Safely applies verified optimizations (with dry-run default and opt-in guardrails)
6. Emits durable audit records and structured telemetry summary
"""
import argparse
from datetime import datetime, timezone
import json
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from costintel.actions import (
    apply_optimization_plan,
    generate_optimization_plans,
    idle_ec2_plan,
    review_plan,
    unused_volume_plan,
)
from costintel.detection import analyze
from costintel.inventory import collect_aws, collect_demo
from costintel.store import SnapshotStore


def run_pipeline(
    store,
    mode,
    account,
    region='ap-south-1',
    profile=None,
    expected_account=None,
    with_metrics=True,
    max_resources=10,
    window_hours=1,
    apply=False,
    action_type='all',
    threshold_cpu=2.0,
    session=None,
    owner='local',
):
    # Step 1: Collect inventory and telemetry
    if mode == 'demo':
        data = collect_demo(region)
    else:
        data = collect_aws(
            region=region,
            profile=profile,
            session=session,
            expected_account=expected_account or account,
            with_metrics=with_metrics,
            max_resources=max_resources,
            window_hours=window_hours,
        )
    snapshot_id = store.save(owner, data)

    # Step 2: Anomaly Detection
    detection_results = analyze(store, owner, mode, account, region)
    outliers = [r for r in detection_results if r.get('status') == 'outlier']
    insufficient_var = [r for r in detection_results if r.get('status') == 'insufficient_variation']
    insufficient_data = [r for r in detection_results if r.get('status') == 'insufficient_data']

    # Step 3: Policy-Driven Plan Generation
    plans = []
    if action_type in ('all', 'review'):
        rev = review_plan(store, account, region)
        if rev:
            plans.append(rev)
    if action_type in ('all', 'stop-ec2'):
        for p in idle_ec2_plan(store, account, region, threshold_cpu=threshold_cpu):
            plans.append(p)
    if action_type in ('all', 'tag-volume'):
        for p in unused_volume_plan(store, account, region):
            plans.append(p)

    # Step 4: Guarded Execution or Dry-Run
    results = []
    if apply:
        if mode == 'demo':
            for p in plans:
                results.append({'id': p['id'], 'status': 'applied_simulated', 'action': p.get('action')})
        else:
            if session is None:
                if not profile:
                    raise ValueError('Applying optimizations in AWS mode requires an authenticated session or --profile')
                session = boto3.Session(profile_name=profile, region_name=region)
            for p in plans:
                try:
                    res = apply_optimization_plan(store, session, p)
                    results.append(res)
                except Exception as exc:
                    results.append({'id': p['id'], 'status': 'failed', 'error': str(exc)})
    else:
        results = [{'id': p['id'], 'status': 'dry_run_pending', 'action': p.get('action')} for p in plans]

    return {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'mode': mode,
        'account': account,
        'region': region,
        'snapshot_id': snapshot_id,
        'inventory': {
            'resources_count': len(data.get('resources', [])),
            'services': sorted(list({r.get('service') for r in data.get('resources', [])})),
        },
        'telemetry': {
            'series_collected': len(data.get('telemetry', {}).get('series', [])) if 'telemetry' in data else 0,
            'api_calls': data.get('telemetry', {}).get('api_calls', 0) if 'telemetry' in data else 0,
        },
        'detection': {
            'total_analyzed': len(detection_results),
            'outliers_count': len(outliers),
            'outliers': [{'resource_id': o['resource_id'], 'metric': o['metric'], 'score': o.get('score')} for o in outliers],
            'insufficient_variation_count': len(insufficient_var),
            'insufficient_data_count': len(insufficient_data),
        },
        'plans_generated': plans,
        'execution': {
            'mode': 'applied' if apply else 'dry_run',
            'results': results,
        },
    }


def format_summary(report):
    lines = [
        "=" * 60,
        f"CostIntel Pipeline Run Summary ({report['mode'].upper()} MODE)",
        "=" * 60,
        f"Account:  {report['account']} | Region: {report['region']}",
        f"Snapshot: {report['snapshot_id']}",
        f"Time:     {report['timestamp']}",
        "-" * 60,
        "1. INVENTORY & TELEMETRY",
        f"   - Discovered resources: {report['inventory']['resources_count']} ({', '.join(report['inventory']['services']) if report['inventory']['services'] else 'None'})",
        f"   - Metric series:        {report['telemetry']['series_collected']} series ({report['telemetry']['api_calls']} CloudWatch API calls)",
        "-" * 60,
        "2. ANOMALY DETECTION (Isolation Forest)",
        f"   - Analyzed series:      {report['detection']['total_analyzed']}",
        f"   - Metric outliers:      {report['detection']['outliers_count']}",
    ]
    for o in report['detection']['outliers']:
        lines.append(f"     * [OUTLIER] {o['metric']} on {o['resource_id']} (score: {o['score']:.4f})")
    lines.extend([
        f"   - Constant baselines:   {report['detection']['insufficient_variation_count']}",
        f"   - Missing history:      {report['detection']['insufficient_data_count']}",
        "-" * 60,
        f"3. OPTIMIZATION ACTIONS ({report['execution']['mode'].upper()})",
        f"   - Generated plans:      {len(report['plans_generated'])}",
    ])
    for p in report['plans_generated']:
        lines.append(f"     * [{p['action'].upper()}] on {p['resource']}: {p.get('reason', '')}")
    for res in report['execution']['results']:
        lines.append(f"       -> Status: {res.get('status')} (id: {res.get('id')})")
    lines.append("=" * 60)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', required=True, choices=['demo', 'aws'])
    parser.add_argument('--account', default='demo-account', help='Account ID (required in AWS mode)')
    parser.add_argument('--region', default='ap-south-1')
    parser.add_argument('--profile', help='AWS profile name')
    parser.add_argument('--expected-account', help='Expected account for validation')
    parser.add_argument('--database', default='.local-data/inventory.sqlite3')
    parser.add_argument('--with-metrics', action='store_true', default=True)
    parser.add_argument('--max-resources', type=int, default=10)
    parser.add_argument('--window-hours', type=int, default=1)
    parser.add_argument('--apply', action='store_true', help='Execute eligible plans (dry-run by default)')
    parser.add_argument('--action-type', choices=['all', 'review', 'stop-ec2', 'tag-volume'], default='all')
    parser.add_argument('--threshold-cpu', type=float, default=2.0)
    parser.add_argument('--json', action='store_true', help='Output raw JSON only')
    args = parser.parse_args(argv)

    if args.mode == 'aws' and args.account == 'demo-account':
        parser.error('--account is required when --mode is aws')
    if args.apply and args.mode == 'aws' and not args.profile:
        parser.error('--apply in AWS mode requires --profile')

    store = SnapshotStore(args.database)
    try:
        report = run_pipeline(
            store=store,
            mode=args.mode,
            account=args.account,
            region=args.region,
            profile=args.profile,
            expected_account=args.expected_account,
            with_metrics=args.with_metrics,
            max_resources=args.max_resources,
            window_hours=args.window_hours,
            apply=args.apply,
            action_type=args.action_type,
            threshold_cpu=args.threshold_cpu,
        )
    except (BotoCoreError, ClientError, ValueError) as exc:
        print(f"Pipeline error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_summary(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

