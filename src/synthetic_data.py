"""
src/synthetic_data.py — Realistic AWS Telemetry and Resource Generator for Simulation Mode.

Generates:
  - EC2 fleet with varied instances, states, and utilization profiles (including idle and runaway spikes)
  - Lambda functions with memory configurations and runtime metadata
  - S3 storage buckets
  - Time-series billing records across core AWS services
  - Historical 7-day telemetry seed data for dashboard visualization and anomaly baseline training
"""

import math
import random
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional



# ─────────────────────────────────────────────────────────────────────────────
# Pre-defined Virtual AWS Infrastructure
# ─────────────────────────────────────────────────────────────────────────────
SIMULATED_INSTANCES = [
    {
        "InstanceId": "i-09f482d87e1a3b90",
        "InstanceType": "t3.xlarge",
        "State": {"Name": "running", "Code": 16},
        "LaunchTime": (datetime.now(timezone.utc) - timedelta(days=45)).isoformat(),
        "Placement": {"AvailabilityZone": "us-east-1a"},
        "Tags": [{"Key": "Name", "Value": "api-cluster-prod-01"}, {"Key": "Environment", "Value": "Production"}]
    },
    {
        "InstanceId": "i-038ba716c90e2114",
        "InstanceType": "t3.medium",
        "State": {"Name": "running", "Code": 16},
        "LaunchTime": (datetime.now(timezone.utc) - timedelta(days=20)).isoformat(),
        "Placement": {"AvailabilityZone": "us-east-1b"},
        "Tags": [{"Key": "Name", "Value": "worker-batch-idle"}, {"Key": "Environment", "Value": "Staging"}]
    },
    {
        "InstanceId": "i-017eef9204bc3811",
        "InstanceType": "c5.2xlarge",
        "State": {"Name": "running", "Code": 16},
        "LaunchTime": (datetime.now(timezone.utc) - timedelta(days=12)).isoformat(),
        "Placement": {"AvailabilityZone": "us-east-1a"},
        "Tags": [{"Key": "Name", "Value": "ml-inference-heavy"}, {"Key": "Environment", "Value": "Production"}]
    },
    {
        "InstanceId": "i-0d71a8bc43f019a2",
        "InstanceType": "t3.small",
        "State": {"Name": "running", "Code": 16},
        "LaunchTime": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        "Placement": {"AvailabilityZone": "us-east-1c"},
        "Tags": [{"Key": "Name", "Value": "redis-replica-cache"}, {"Key": "Environment", "Value": "Production"}]
    },
    {
        "InstanceId": "i-04a29c118e932145",
        "InstanceType": "m5.large",
        "State": {"Name": "stopped", "Code": 80},
        "LaunchTime": (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),
        "Placement": {"AvailabilityZone": "us-east-1b"},
        "Tags": [{"Key": "Name", "Value": "staging-qa-runner"}, {"Key": "Environment", "Value": "Staging"}]
    },
    {
        "InstanceId": "i-08b3c99021aef773",
        "InstanceType": "t3.micro",
        "State": {"Name": "running", "Code": 16},
        "LaunchTime": (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(),
        "Placement": {"AvailabilityZone": "us-east-1a"},
        "Tags": [{"Key": "Name", "Value": "telemetry-agent"}, {"Key": "Environment", "Value": "Infrastructure"}]
    }
]

SIMULATED_FUNCTIONS = [
    {
        "FunctionName": "image-resizer-prod",
        "FunctionArn": "arn:aws:lambda:us-east-1:000000000000:function:image-resizer-prod",
        "Runtime": "python3.11",
        "Role": "arn:aws:iam::000000000000:role/lambda-role",
        "Handler": "handler.lambda_handler",
        "CodeSize": 4194304,
        "Description": "Dynamic image optimization and thumbnail generator",
        "Timeout": 30,
        "MemorySize": 1024,
        "LastModified": (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    },
    {
        "FunctionName": "nightly-cost-export",
        "FunctionArn": "arn:aws:lambda:us-east-1:000000000000:function:nightly-cost-export",
        "Runtime": "python3.11",
        "Role": "arn:aws:iam::000000000000:role/lambda-role",
        "Handler": "export.handler",
        "CodeSize": 2097152,
        "Description": "Nightly AWS Cost & Usage report aggregator to S3",
        "Timeout": 300,
        "MemorySize": 512,
        "LastModified": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    },
    {
        "FunctionName": "user-auth-authorizer",
        "FunctionArn": "arn:aws:lambda:us-east-1:000000000000:function:user-auth-authorizer",
        "Runtime": "nodejs18.x",
        "Role": "arn:aws:iam::000000000000:role/lambda-role",
        "Handler": "index.handler",
        "CodeSize": 1048576,
        "Description": "Edge JWT verification and auth token parsing",
        "Timeout": 10,
        "MemorySize": 256,
        "LastModified": (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    },
    {
        "FunctionName": "webhook-ingestor",
        "FunctionArn": "arn:aws:lambda:us-east-1:000000000000:function:webhook-ingestor",
        "Runtime": "python3.10",
        "Role": "arn:aws:iam::000000000000:role/lambda-role",
        "Handler": "webhook.main",
        "CodeSize": 3145728,
        "Description": "Stripe, GitHub, and Slack webhook ingestion receiver",
        "Timeout": 60,
        "MemorySize": 512,
        "LastModified": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    }
]

SIMULATED_BUCKETS = [
    {"Name": "cloud-cost-intelligence-cur-bucket", "CreationDate": datetime(2025, 1, 15, tzinfo=timezone.utc)},
    {"Name": "ml-checkpoints-prod-2025", "CreationDate": datetime(2025, 2, 1, tzinfo=timezone.utc)},
    {"Name": "static-assets-web-cdn", "CreationDate": datetime(2025, 2, 10, tzinfo=timezone.utc)},
    {"Name": "system-audit-logs-archive", "CreationDate": datetime(2025, 1, 1, tzinfo=timezone.utc)},
]


# ─────────────────────────────────────────────────────────────────────────────
# Generator Functions
# ─────────────────────────────────────────────────────────────────────────────
def generate_ec2_fleet() -> List[Dict[str, Any]]:
    """Returns the simulated EC2 fleet."""
    import copy
    return copy.deepcopy(SIMULATED_INSTANCES)


def generate_lambda_functions() -> List[Dict[str, Any]]:
    """Returns the simulated Lambda function list."""
    import copy
    return copy.deepcopy(SIMULATED_FUNCTIONS)


def generate_s3_buckets() -> List[Dict[str, Any]]:
    """Returns the simulated S3 bucket list."""
    import copy
    return copy.deepcopy(SIMULATED_BUCKETS)


def generate_instance_cpu_datapoint(instance_id: str) -> float:
    """
    Generates realistic CPU utilization percentage for an instance.
    Includes natural variance and designated anomalous behavior.
    """
    if instance_id == "i-038ba716c90e2114":
        # Consistently idle instance (~0.3% - 0.7%) -> triggers idle_instance anomaly
        return round(random.uniform(0.32, 0.78), 4)

    elif instance_id == "i-017eef9204bc3811":
        # Sustained CPU spike (~88% - 97%) -> triggers cpu_spike anomaly
        return round(random.uniform(88.4, 96.8), 4)

    elif instance_id == "i-09f482d87e1a3b90":
        # Active production workload (~35% - 55%)
        return round(random.uniform(35.0, 55.0), 4)

    elif instance_id == "i-0d71a8bc43f019a2":
        # Cache replica (~18% - 28%)
        return round(random.uniform(18.0, 28.0), 4)

    elif instance_id == "i-08b3c99021aef773":
        # Light daemon agent (~5% - 12%)
        return round(random.uniform(5.0, 12.0), 4)

    return round(random.uniform(15.0, 35.0), 4)


def generate_billing_records(timestamp: Optional[str] = None) -> List[Dict[str, Any]]:
    """Generates current billing snapshot records across services."""
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    return [
        {
            "timestamp": ts,
            "service": "Amazon EC2",
            "region": "us-east-1",
            "cost_usd": round(random.uniform(0.38, 0.46), 4),
            "resource_id": "i-09f482d87e1a3b90"
        },
        {
            "timestamp": ts,
            "service": "AWS Lambda",
            "region": "us-east-1",
            "cost_usd": round(random.uniform(0.06, 0.12), 4),
            "resource_id": "image-resizer-prod"
        },
        {
            "timestamp": ts,
            "service": "Amazon RDS",
            "region": "us-east-1",
            "cost_usd": round(random.uniform(0.62, 0.68), 4),
            "resource_id": "cost-intelligence-db"
        },
        {
            "timestamp": ts,
            "service": "Amazon S3",
            "region": "us-east-1",
            "cost_usd": round(random.uniform(0.16, 0.20), 4),
            "resource_id": "ml-checkpoints-prod-2025"
        },
        {
            "timestamp": ts,
            "service": "Amazon CloudWatch",
            "region": "us-east-1",
            "cost_usd": round(random.uniform(0.04, 0.08), 4),
            "resource_id": "cloudwatch-metrics"
        }
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Historical Data Seeder for In-Memory Store
# ─────────────────────────────────────────────────────────────────────────────
def seed_historical_data(force: bool = False) -> None:
    """
    Seeds the in-memory data store with 7 days of realistic time-series data.
    Ensures the dashboard charts and statistical detectors have rich baseline data.
    """
    from src.data_store import get_in_memory_table

    cost_table = get_in_memory_table("CostTelemetry")
    anomaly_table = get_in_memory_table("AnomalyEvents")
    audit_table = get_in_memory_table("OptimizationAudit")

    if cost_table.item_count > 0 and not force:
        return  # Already seeded

    now = datetime.now(timezone.utc)

    # 1. Generate 7 days of 6-hour interval billing and utilization records
    services_profile = {
        "Amazon EC2": {"base": 0.42, "var": 0.04, "spike_pt": 14, "spike_add": 0.85},
        "AWS Lambda": {"base": 0.08, "var": 0.02, "spike_pt": 22, "spike_add": 0.45},
        "Amazon RDS": {"base": 0.65, "var": 0.03, "spike_pt": -1, "spike_add": 0.0},
        "Amazon S3": {"base": 0.18, "var": 0.01, "spike_pt": -1, "spike_add": 0.0},
        "Amazon CloudWatch": {"base": 0.06, "var": 0.01, "spike_pt": -1, "spike_add": 0.0},
    }

    instances = [i for i in SIMULATED_INSTANCES if i.get("State", {}).get("Name") == "running"]

    # 28 intervals (4 per day * 7 days)
    for idx in range(28, -1, -1):
        point_time = now - timedelta(hours=idx * 6)
        ts_str = point_time.isoformat()

        # Billing items
        for svc_name, cfg in services_profile.items():
            cost = cfg["base"] + math.sin(idx * 0.5) * cfg["var"]
            if idx == cfg["spike_pt"]:
                cost += cfg["spike_add"]
            cost = max(0.01, round(cost, 4))

            cost_table.put_item(Item={
                "resource_id": f"res-{svc_name.lower().replace(' ', '-')}",
                "timestamp": ts_str,
                "metric_type": "billing",
                "service": svc_name,
                "region": "us-east-1",
                "cost_usd": str(cost)
            })

        # Utilization items for running EC2 instances
        for inst in instances:
            iid = inst["InstanceId"]
            cpu = generate_instance_cpu_datapoint(iid)
            cost_table.put_item(Item={
                "resource_id": iid,
                "timestamp": ts_str,
                "metric_type": "utilization",
                "service": "Amazon EC2",
                "region": "us-east-1",
                "cpu_utilization": str(cpu)
            })

    # 2. Add inventory records
    inventory_ts = now.isoformat()
    for inst in SIMULATED_INSTANCES:
        cost_table.put_item(Item={
            "resource_id": inst["InstanceId"],
            "timestamp": inventory_ts,
            "metric_type": "inventory",
            "service": "Amazon EC2",
            "region": "us-east-1",
            "state": inst["State"]["Name"],
            "instance_type": inst["InstanceType"]
        })

    for func in SIMULATED_FUNCTIONS:
        cost_table.put_item(Item={
            "resource_id": func["FunctionName"],
            "timestamp": inventory_ts,
            "metric_type": "inventory",
            "service": "AWS Lambda",
            "region": "us-east-1",
            "state": "active",
            "instance_type": func["Runtime"]
        })

    for bucket in SIMULATED_BUCKETS:
        cost_table.put_item(Item={
            "resource_id": bucket["Name"],
            "timestamp": inventory_ts,
            "metric_type": "inventory",
            "service": "Amazon S3",
            "region": "us-east-1",
            "state": "active",
            "instance_type": "bucket"
        })

    # 3. Seed initial high-signal anomalies
    seed_anomalies = [
        {
            "anomaly_id": "anom-ec2-cpu-9842",
            "timestamp": (now - timedelta(minutes=45)).isoformat(),
            "anomaly_type": "cpu_spike",
            "model": "statistical_time_series",
            "confidence": "0.94",
            "status": "actioned",
            "resource_id": "i-017eef9204bc3811",
            "action_taken": "cap_lambda_concurrency",
            "details": "Sustained CPU at 94.8% for >120m without autoscaling trigger"
        },
        {
            "anomaly_id": "anom-lambda-runaway-01",
            "timestamp": (now - timedelta(hours=2, minutes=15)).isoformat(),
            "anomaly_type": "runaway_function",
            "model": "multivariate_rules",
            "confidence": "0.97",
            "status": "actioned",
            "resource_id": "image-resizer-prod",
            "action_taken": "cap_lambda_concurrency",
            "details": "High concurrent invocations exceeding normal variance threshold"
        },
        {
            "anomaly_id": "anom-ec2-idle-8821",
            "timestamp": (now - timedelta(hours=5, minutes=10)).isoformat(),
            "anomaly_type": "idle_instance",
            "model": "statistical_time_series",
            "confidence": "0.98",
            "status": "actioned",
            "resource_id": "i-038ba716c90e2114",
            "action_taken": "stop_ec2_instance",
            "details": "Average CPU utilization < 0.5% over past 72 consecutive hours"
        },
        {
            "anomaly_id": "anom-cost-spike-4412",
            "timestamp": (now - timedelta(days=1, hours=3)).isoformat(),
            "anomaly_type": "cost_spike",
            "model": "multivariate_rules",
            "confidence": "0.92",
            "status": "actioned",
            "resource_id": "i-09f482d87e1a3b90",
            "action_taken": "tag_resource_for_review",
            "details": "Hourly bill climbed 280% vs trailing 7-day moving average"
        }
    ]
    for anom in seed_anomalies:
        anomaly_table.put_item(Item=anom)

    # 4. Seed initial audit records
    seed_audits = [
        {
            "action_id": "act-opt-01",
            "timestamp": (now - timedelta(minutes=44)).isoformat(),
            "anomaly_id": "anom-ec2-cpu-9842",
            "anomaly_type": "cpu_spike",
            "model": "statistical_time_series",
            "confidence": "0.94",
            "action_taken": "put_function_concurrency",
            "resource_id": "image-resizer-prod",
            "resource_type": "Lambda",
            "status": "actioned",
            "estimated_saving_usd": "0.005",
            "rollback_command": "aws lambda delete-function-concurrency --function-name image-resizer-prod"
        },
        {
            "action_id": "act-opt-02",
            "timestamp": (now - timedelta(hours=2, minutes=14)).isoformat(),
            "anomaly_id": "anom-lambda-runaway-01",
            "anomaly_type": "runaway_function",
            "model": "multivariate_rules",
            "confidence": "0.97",
            "action_taken": "put_function_concurrency",
            "resource_id": "image-resizer-prod",
            "resource_type": "Lambda",
            "status": "actioned",
            "estimated_saving_usd": "0.005",
            "rollback_command": "aws lambda delete-function-concurrency --function-name image-resizer-prod"
        },
        {
            "action_id": "act-opt-03",
            "timestamp": (now - timedelta(hours=5, minutes=9)).isoformat(),
            "anomaly_id": "anom-ec2-idle-8821",
            "anomaly_type": "idle_instance",
            "model": "statistical_time_series",
            "confidence": "0.98",
            "action_taken": "stop_instances",
            "resource_id": "i-038ba716c90e2114",
            "resource_type": "EC2",
            "status": "actioned",
            "estimated_saving_usd": "0.012",
            "rollback_command": "aws ec2 start-instances --instance-ids i-038ba716c90e2114"
        },
        {
            "action_id": "act-opt-04",
            "timestamp": (now - timedelta(days=1, hours=2)).isoformat(),
            "anomaly_id": "anom-cost-spike-4412",
            "anomaly_type": "cost_spike",
            "model": "multivariate_rules",
            "confidence": "0.92",
            "action_taken": "create_tags",
            "resource_id": "i-09f482d87e1a3b90",
            "resource_type": "EC2",
            "status": "actioned",
            "estimated_saving_usd": "0",
            "rollback_command": "aws ec2 delete-tags --resources i-09f482d87e1a3b90 --tags Key=review-needed"
        }
    ]
    for act in seed_audits:
        audit_table.put_item(Item=act)
