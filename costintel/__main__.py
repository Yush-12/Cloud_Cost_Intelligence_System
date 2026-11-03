"""Local collector, pipeline orchestrator, and read-only viewer."""
import argparse
import json
import sys

from botocore.exceptions import BotoCoreError, ClientError

from costintel.inventory import collect_aws, collect_demo
from costintel.store import SnapshotStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["collect", "serve", "pipeline"])
    parser.add_argument("--mode", required=True, choices=["demo", "aws"])
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--profile")
    parser.add_argument("--expected-account", help="Abort if STS returns a different account")
    parser.add_argument("--account", help="Account to view (required for serve/pipeline in AWS mode)")
    parser.add_argument("--database", default=".local-data/inventory.sqlite3")
    parser.add_argument("--public-demo", action="store_true")
    parser.add_argument("--host", choices=["127.0.0.1","0.0.0.0"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--with-metrics", action="store_true", help="Read the last hour of CloudWatch metrics")
    parser.add_argument("--max-resources", type=int, default=10, help="Telemetry resource limit (1-100, default 10)")
    parser.add_argument("--window-hours", type=int, default=1, help="CloudWatch lookback in hours (1-48)")
    parser.add_argument("--apply", action="store_true", help="Execute optimizations during pipeline (dry-run by default)")
    parser.add_argument("--action-type", choices=["all", "review", "stop-ec2", "tag-volume", "contain-lambda"], default="all")
    parser.add_argument("--threshold-cpu", type=float, default=2.0)
    args = parser.parse_args(argv)
    if not 1 <= args.max_resources <= 100:
        parser.error("--max-resources must be between 1 and 100")
    if not 1 <= args.window_hours <= 48:
        parser.error("--window-hours must be between 1 and 48")
    if args.with_metrics and (args.mode != "aws" or args.command not in ("collect", "pipeline")):
        parser.error("--with-metrics requires collect or pipeline in --mode aws")
    if args.command in ("serve", "pipeline") and args.mode == "aws" and not args.account:
        parser.error(f"{args.command} --mode aws requires --account")
    if args.host == '0.0.0.0' and not args.public_demo:
        parser.error('Network binding requires --public-demo (AWS login disabled)')
    store = SnapshotStore(args.database)
    if args.command == "serve":
        from costintel.web import create_app
        app = create_app(store, "local", args.mode,
                         args.account if args.mode == "aws" else "demo-account", args.region, deployment="demo" if args.public_demo else "local")
        app.run(host=args.host, port=args.port, debug=False)
        return 0
    if args.command == "pipeline":
        from costintel.pipeline import run_pipeline, format_summary
        try:
            report = run_pipeline(
                store=store,
                mode=args.mode,
                account=args.account if args.mode == "aws" else "demo-account",
                region=args.region,
                profile=args.profile,
                expected_account=args.expected_account or args.account,
                with_metrics=args.with_metrics,
                max_resources=args.max_resources,
                window_hours=args.window_hours,
                apply=args.apply,
                action_type=args.action_type,
                threshold_cpu=args.threshold_cpu,
            )
            print(format_summary(report))
            return 0
        except (BotoCoreError, ClientError, ValueError) as exc:
            print(f"Pipeline failed: {exc}", file=sys.stderr)
            return 1
    try:
        data = (collect_demo(args.region) if args.mode == "demo" else
                collect_aws(args.region, args.profile, args.expected_account,
                            with_metrics=args.with_metrics, max_resources=args.max_resources,
                            window_hours=args.window_hours))
        snapshot_id = store.save("local", data)
    except (BotoCoreError, ClientError, ValueError) as exc:
        # Do not dump credential/configuration details into console or web responses.
        code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
        print(f"Collection failed ({code}). Check profile, region, account and read permissions. "
              "No new snapshot was saved; previous snapshots remain historical.", file=sys.stderr)
        return 1
    print(json.dumps({"snapshot_id": snapshot_id, **data}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
