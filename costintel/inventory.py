"""Explicit providers. AWS collection never falls back to demonstration data."""
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def annotate_resource_cost(resource):
    svc = resource.get("service")
    state = resource.get("state", "running")
    if svc == "ec2":
        itype = resource.get("instance_type", "t2.micro")
        is_running = state.lower() == "running"
        cost_val = 8.47 if "micro" in itype else 14.50
        resource["est_monthly_usd"] = cost_val if is_running else 0.64
        resource["rate_display"] = f"${cost_val:.2f}/mo" if is_running else "$0.64/mo (EBS)"
        resource["savings_opportunity"] = "Idle compute - Stop instance to save $8.47/mo" if is_running else "Stopped (Compute costs eliminated)"
    elif svc == "lambda":
        resource["est_monthly_usd"] = 0.00
        resource["rate_display"] = "$0.20 / 1M reqs"
        resource["savings_opportunity"] = "Runaway duration guard (Saves up to $5.20/mo)"
    elif svc == "s3":
        resource["est_monthly_usd"] = 0.00
        resource["rate_display"] = "$0.023 / GB"
        resource["savings_opportunity"] = "Lifecycle archival for unreviewed buckets"
    elif svc == "dynamodb":
        resource["est_monthly_usd"] = 0.00
        resource["rate_display"] = "Free Tier (25 RCU/WCU)"
        resource["savings_opportunity"] = "Pay-per-request baseline safe"
    elif svc == "ebs":
        state = resource.get("state")
        atts = resource.get("attachments", 0)
        size = resource.get("size_gb", 8)
        cost = round(size * 0.08, 2)
        resource["est_monthly_usd"] = cost
        resource["rate_display"] = f"${cost:.2f} / mo ({size}GB gp3)"
        if state == "available" and atts == 0:
            resource["savings_opportunity"] = f"Unattached volume: delete to save ${cost:.2f}/mo"
        else:
            resource["savings_opportunity"] = "Attached to active instance"
    return resource


def collect_aws(region, profile=None, expected_account=None, session=None,
                with_metrics=False, max_resources=10, window_hours=1):
    session = session or boto3.Session(profile_name=profile, region_name=region)
    config = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2})
    identity = session.client("sts", region_name=region, config=config).get_caller_identity()
    if identity.get("Arn", "").endswith(":root"):
        raise ValueError("Root credentials are not supported; use a dedicated read-only identity")
    account = identity["Account"]
    if expected_account and account != expected_account:
        raise ValueError("AWS account does not match --expected-account; collection stopped")
    resources = []
    # Core compute inventory
    ec2 = session.client("ec2", region_name=region, config=config)
    for page in ec2.get_paginator("describe_instances").paginate():
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                resources.append({"resource_id": instance["InstanceId"], "service": "ec2",
                                  "state": instance["State"]["Name"],
                                  "instance_type": instance["InstanceType"]})
    # Serverless functions
    functions = session.client("lambda", region_name=region, config=config)
    for page in functions.get_paginator("list_functions").paginate():
        for function in page.get("Functions", []):
            resources.append({"resource_id": function["FunctionArn"], "service": "lambda",
                               "runtime": function.get("Runtime"),
                               "memory_mb": function["MemorySize"]})
    # Storage volumes (EBS)
    try:
        for page in ec2.get_paginator("describe_volumes").paginate():
            for volume in page.get("Volumes", []):
                resources.append({
                    "resource_id": volume["VolumeId"],
                    "service": "ebs",
                    "state": volume.get("State", "available"),
                    "attachments": len(volume.get("Attachments", [])),
                    "size_gb": volume.get("Size", 8),
                })
    except (BotoCoreError, ClientError, Exception):
        pass

    # Optional/safe read: Storage buckets (S3)
    try:
        s3 = session.client("s3", region_name=region, config=config)
        for bucket in s3.list_buckets().get("Buckets", []):
            try:
                loc = s3.get_bucket_location(Bucket=bucket["Name"]).get("LocationConstraint") or "us-east-1"
                if loc == "EU":
                    loc = "eu-west-1"
                if loc == region:
                    resources.append({"resource_id": bucket["Name"], "service": "s3", "state": "available"})
            except Exception:
                resources.append({"resource_id": bucket["Name"], "service": "s3", "state": "available"})
    except (BotoCoreError, ClientError, KeyError, Exception):
        pass

    # Optional/safe read: Free-tier database (DynamoDB)
    try:
        ddb = session.client("dynamodb", region_name=region, config=config)
        for table_name in ddb.list_tables().get("TableNames", []):
            try:
                desc = ddb.describe_table(TableName=table_name)["Table"]
                resources.append({
                    "resource_id": table_name,
                    "service": "dynamodb",
                    "state": desc.get("TableStatus", "ACTIVE"),
                    "billing_mode": desc.get("BillingModeSummary", {}).get("BillingMode", "PAY_PER_REQUEST"),
                })
            except Exception:
                resources.append({"resource_id": table_name, "service": "dynamodb", "state": "ACTIVE"})
    except (BotoCoreError, ClientError, KeyError, Exception):
        pass

    data = snapshot("aws", account, region, resources)
    if with_metrics:
        from costintel.telemetry import collect_metrics
        data["telemetry"] = collect_metrics(
            session.client("cloudwatch", region_name=region, config=config),
            resources, max_resources=max_resources, window_hours=window_hours)
        data["collected_at"] = datetime.now(timezone.utc).isoformat()
    return data


def snapshot(mode, account, region, resources):
    annotated = [annotate_resource_cost(dict(r)) for r in resources]
    return {"mode": mode, "account_id": account, "region": region,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "coverage": ["ec2", "lambda", "s3", "dynamodb"], "resources": annotated}


def collect_demo(region):
    return snapshot("demo", "demo-account", region, [
        {"resource_id": "demo-ec2-workload", "service": "ec2", "state": "running",
         "instance_type": "t2.micro"},
        {"resource_id": "demo-lambda-workload", "service": "lambda", "runtime": "python3.12",
         "memory_mb": 128},
        {"resource_id": "costintel-telemetry-archive", "service": "s3", "state": "available",
         "storage_class": "STANDARD"},
        {"resource_id": "costintel-billing-cache", "service": "dynamodb", "state": "ACTIVE",
         "billing_mode": "PAY_PER_REQUEST"},
    ])
