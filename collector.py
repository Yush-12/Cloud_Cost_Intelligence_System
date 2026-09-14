import os
import logging
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from botocore.exceptions import ClientError

from aws_clients import (
    get_table,
    get_ec2_client,
    get_cloudwatch_client,
    get_lambda_client,
    get_s3_client,
    REGION
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("collector")

TABLE_NAME = os.getenv("DYNAMODB_TABLE", "CostTelemetry")


# ─────────────────────────────────────────
# STREAM 1: Billing Metrics (Cost Explorer Mock)
# ─────────────────────────────────────────
def get_cost_data_mock():
    timestamp = datetime.now(timezone.utc).isoformat()
    return [
        {
            "timestamp": timestamp,
            "service": "Amazon EC2",
            "region": REGION,
            "cost_usd": 0.023,
            "resource_id": "i-0abc123def456"
        },
        {
            "timestamp": timestamp,
            "service": "AWS Lambda",
            "region": REGION,
            "cost_usd": 0.0,
            "resource_id": "cost-telemetry-collector"
        },
        {
            "timestamp": timestamp,
            "service": "Amazon RDS",
            "region": REGION,
            "cost_usd": 0.017,
            "resource_id": "cost-intelligence-db"
        }
    ]


def collect_billing_metrics():
    logger.info("[1/3] Collecting billing metrics...")
    records = get_cost_data_mock()

    try:
        table = get_table(TABLE_NAME)
        for record in records:
            table.put_item(Item={
                "resource_id": record["resource_id"],
                "timestamp": record["timestamp"],
                "metric_type": "billing",
                "service": record["service"],
                "region": record["region"],
                "cost_usd": str(record["cost_usd"])
            })
        logger.info(f"Written {len(records)} billing records to DynamoDB table '{TABLE_NAME}'")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            logger.error(
                f"DynamoDB table '{TABLE_NAME}' does not exist! "
                "Please run 'python setup_aws.py' to initialize the required tables."
            )
        else:
            logger.error(f"Error writing billing metrics: {e}")


# ─────────────────────────────────────────
# STREAM 2: Utilization Metrics (CloudWatch)
# ─────────────────────────────────────────
def collect_utilization_metrics(instances=None):
    logger.info("[2/3] Collecting utilization metrics...")
    ec2 = get_ec2_client()
    cloudwatch = get_cloudwatch_client()

    try:
        if instances is None:
            response = ec2.describe_instances(
                Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
            )
            instances = [i for r in response["Reservations"] for i in r["Instances"]]
        else:
            instances = [i for i in instances if i.get("State", {}).get("Name") == "running"]
    except ClientError as e:
        logger.error(f"Failed to describe EC2 instances: {e}")
        return

    if not instances:
        logger.warning("No running EC2 instances found — skipping CloudWatch pull")
        return

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=15)
    table = get_table(TABLE_NAME)

    for instance in instances:
        instance_id = instance["InstanceId"]
        try:
            cw_response = cloudwatch.get_metric_statistics(
                Namespace="AWS/EC2",
                MetricName="CPUUtilization",
                Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=300,
                Statistics=["Average"]
            )
            datapoints = cw_response.get("Datapoints", [])
            cpu_value = datapoints[-1]["Average"] if datapoints else 0.0

            table.put_item(Item={
                "resource_id": instance_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metric_type": "utilization",
                "service": "Amazon EC2",
                "region": REGION,
                "cpu_utilization": str(round(cpu_value, 4))
            })
            logger.info(f"EC2 {instance_id}: CPU {round(cpu_value, 2)}%")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                logger.error(f"DynamoDB table '{TABLE_NAME}' does not exist! Run 'python setup_aws.py'.")
                break
            else:
                logger.error(f"Error collecting metrics for instance {instance_id}: {e}")


# ─────────────────────────────────────────
# STREAM 3: Resource Inventory
# ─────────────────────────────────────────
def collect_resource_inventory(instances=None):
    logger.info("[3/3] Collecting resource inventory...")
    timestamp = datetime.now(timezone.utc).isoformat()
    ec2 = get_ec2_client()
    lambda_client = get_lambda_client()
    s3 = get_s3_client()
    table = get_table(TABLE_NAME)

    # EC2 instances
    try:
        if instances is None:
            ec2_resp = ec2.describe_instances()
            instances = [i for r in ec2_resp["Reservations"] for i in r["Instances"]]

        for instance in instances:
            table.put_item(Item={
                "resource_id": instance["InstanceId"],
                "timestamp": timestamp,
                "metric_type": "inventory",
                "service": "Amazon EC2",
                "region": REGION,
                "state": instance["State"]["Name"],
                "instance_type": instance["InstanceType"]
            })
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            logger.error(f"DynamoDB table '{TABLE_NAME}' does not exist! Run 'python setup_aws.py'.")
            return []
        logger.error(f"Error collecting EC2 inventory: {e}")
        instances = []

    # Lambda functions
    try:
        lambda_resp = lambda_client.list_functions()
        for func in lambda_resp.get("Functions", []):
            table.put_item(Item={
                "resource_id": func["FunctionName"],
                "timestamp": timestamp,
                "metric_type": "inventory",
                "service": "AWS Lambda",
                "region": REGION,
                "state": "active",
                "instance_type": func.get("Runtime", "unknown")
            })
    except ClientError as e:
        logger.error(f"Error collecting Lambda inventory: {e}")

    # S3 buckets
    try:
        s3_resp = s3.list_buckets()
        for bucket in s3_resp.get("Buckets", []):
            table.put_item(Item={
                "resource_id": bucket["Name"],
                "timestamp": timestamp,
                "metric_type": "inventory",
                "service": "Amazon S3",
                "region": REGION,
                "state": "active",
                "instance_type": "bucket"
            })
    except ClientError as e:
        logger.error(f"Error collecting S3 inventory: {e}")

    logger.info("Inventory snapshot written to DynamoDB")
    return instances


# ─────────────────────────────────────────
# MAIN COLLECTOR RUN
# ─────────────────────────────────────────
def run_collector():
    start_time = datetime.now(timezone.utc).isoformat()
    logger.info(f"Collector run started at {start_time}")
    collect_billing_metrics()
    instances = collect_resource_inventory()
    collect_utilization_metrics(instances=instances)
    logger.info("Collector run complete.")


if __name__ == "__main__":
    run_collector()