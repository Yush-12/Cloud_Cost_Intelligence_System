"""Explicit providers. AWS collection never falls back to demonstration data."""
from datetime import datetime, timezone

import boto3
from botocore.config import Config


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
    # Publish only a complete snapshot: an API error aborts collection.
    ec2 = session.client("ec2", region_name=region, config=config)
    for page in ec2.get_paginator("describe_instances").paginate():
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                resources.append({"resource_id": instance["InstanceId"], "service": "ec2",
                                  "state": instance["State"]["Name"],
                                  "instance_type": instance["InstanceType"]})
    functions = session.client("lambda", region_name=region, config=config)
    for page in functions.get_paginator("list_functions").paginate():
        for function in page.get("Functions", []):
            resources.append({"resource_id": function["FunctionArn"], "service": "lambda",
                              "runtime": function.get("Runtime"),
                              "memory_mb": function["MemorySize"]})
    data = snapshot("aws", account, region, resources)
    if with_metrics:
        from costintel.telemetry import collect_metrics
        data["telemetry"] = collect_metrics(
            session.client("cloudwatch", region_name=region, config=config),
            resources, max_resources=max_resources, window_hours=window_hours)
        data["collected_at"] = datetime.now(timezone.utc).isoformat()
    return data


def snapshot(mode, account, region, resources):
    return {"mode": mode, "account_id": account, "region": region,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "coverage": ["ec2", "lambda"], "resources": resources}


def collect_demo(region):
    return snapshot("demo", "demo-account", region, [
        {"resource_id": "demo-ec2-idle", "service": "ec2", "state": "running",
         "instance_type": "demo-instance"},
        {"resource_id": "demo-lambda", "service": "lambda", "runtime": "python3.12",
         "memory_mb": 128},
    ])
