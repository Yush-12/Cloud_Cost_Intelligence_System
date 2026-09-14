import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

# Ensure repository root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db_utils import scan_all, safe_float, safe_int
from src.aws_clients import (
    get_table,
    get_ec2_client,
    get_lambda_client,
    REGION
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("optimization_engine")

MAX_ACTIONS_PER_HOUR = safe_int(os.getenv("MAX_ACTIONS_PER_HOUR"), 5)
ANOMALY_TABLE_NAME = os.getenv("ANOMALY_TABLE", "AnomalyEvents")
AUDIT_TABLE_NAME = os.getenv("AUDIT_TABLE", "OptimizationAudit")

# ─────────────────────────────────────────
# RULE TABLE: Anomaly Type → Safe Action
# ─────────────────────────────────────────
ACTION_RULES = {
    "idle_instance":    "stop_ec2_instance",
    "cpu_spike":        "cap_lambda_concurrency",
    "runaway_function": "cap_lambda_concurrency",
    "cost_spike":       "tag_resource_for_review",
    "orphaned_volume":  "tag_resource_for_review",
}


# ─────────────────────────────────────────
# FETCH PENDING ANOMALIES
# ─────────────────────────────────────────
def fetch_pending_anomalies():
    logger.info(f"Fetching pending anomalies from DynamoDB table '{ANOMALY_TABLE_NAME}'...")
    try:
        anomaly_table = get_table(ANOMALY_TABLE_NAME)
        items = scan_all(anomaly_table, Attr("status").eq("pending"))
        logger.info(f"Found {len(items)} pending anomalies")
        return items
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            logger.error(
                f"DynamoDB table '{ANOMALY_TABLE_NAME}' does not exist! "
                "Please run 'python scripts/setup_aws.py' to initialize the required tables."
            )
        else:
            logger.error(f"Failed to fetch pending anomalies: {e}")
        return []


# ─────────────────────────────────────────
# ACTIONS
# ─────────────────────────────────────────
def stop_ec2_instance(anomaly):
    """
    Stops idle EC2 instances. Safe — instances can be restarted anytime.
    """
    logger.info("Action: stop_ec2_instance")
    ec2_client = get_ec2_client()

    response = ec2_client.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    )

    instances = [
        i for r in response["Reservations"] for i in r["Instances"]
    ]

    if not instances:
        return {
            "status": "skipped",
            "reason": "All instances already stopped",
            "estimated_saving_usd": 0
        }

    target = instances[0]
    instance_id = target["InstanceId"]
    instance_type = target.get("InstanceType", "unknown")

    ec2_client.stop_instances(InstanceIds=[instance_id])
    logger.info(f"Stopped EC2 instance {instance_id} ({instance_type})")

    return {
        "status": "actioned",
        "resource_id": instance_id,
        "resource_type": "EC2",
        "action": "stop_instances",
        "rollback_command": f"aws ec2 start-instances --instance-ids {instance_id}",
        "estimated_saving_usd": 0.012
    }


def cap_lambda_concurrency(anomaly):
    logger.info("Action: cap_lambda_concurrency")
    lambda_client = get_lambda_client()
    audit_table = get_table(AUDIT_TABLE_NAME)

    response = lambda_client.list_functions()
    functions = response.get("Functions", [])

    if not functions:
        return {
            "status": "skipped",
            "reason": "No Lambda functions found",
            "estimated_saving_usd": 0
        }

    account_settings = lambda_client.get_account_settings()
    total_concurrency = account_settings.get("AccountLimit", {}).get("ConcurrentExecutions", 10)
    logger.info(f"Account concurrency limit: {total_concurrency}")

    if total_concurrency <= 10:
        logger.warning("Concurrency limit too low to reserve — falling back to tagging")
        tagged = []
        for func in functions:
            func_name = func["FunctionName"]
            audit_table.put_item(Item={
                "action_id": f"flag-{func_name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "anomaly_id": anomaly.get("anomaly_id", "unknown"),
                "anomaly_type": anomaly.get("anomaly_type", "unknown"),
                "model": anomaly.get("model", "unknown"),
                "confidence": str(anomaly.get("confidence", 0)),
                "action_taken": "flagged_for_review",
                "resource_id": func_name,
                "resource_type": "Lambda",
                "status": "actioned",
                "reason": f"Account concurrency limit ({total_concurrency}) too low to cap — flagged for manual review",
                "rollback_command": "none",
                "estimated_saving_usd": "0"
            })
            logger.info(f"Flagged {func_name} for review in audit table")
            tagged.append(func_name)

        return {
            "status": "actioned",
            "resource_id": ", ".join(tagged),
            "resource_type": "Lambda",
            "action": "flagged_for_review",
            "rollback_command": "none",
            "estimated_saving_usd": 0
        }

    results = []
    for func in functions:
        func_name = func["FunctionName"]
        cap = max(10, total_concurrency // 2)
        lambda_client.put_function_concurrency(
            FunctionName=func_name,
            ReservedConcurrentExecutions=cap
        )
        logger.info(f"Capped {func_name} concurrency to {cap}")
        results.append(func_name)

    return {
        "status": "actioned",
        "resource_id": ", ".join(results),
        "resource_type": "Lambda",
        "action": "put_function_concurrency",
        "rollback_command": "aws lambda delete-function-concurrency --function-name <name>",
        "estimated_saving_usd": 0.005
    }


def tag_resource_for_review(anomaly):
    """
    Tags anomalous resources with review-needed=true.
    Safe — no infrastructure changes.
    """
    logger.info("Action: tag_resource_for_review")
    ec2_client = get_ec2_client()

    response = ec2_client.describe_instances()
    instances = [
        i for r in response["Reservations"] for i in r["Instances"]
    ]

    tagged = []
    for instance in instances:
        instance_id = instance["InstanceId"]
        ec2_client.create_tags(
            Resources=[instance_id],
            Tags=[
                {"Key": "review-needed", "Value": "true"},
                {"Key": "anomaly-type", "Value": anomaly.get("anomaly_type", "unknown")},
                {"Key": "flagged-at", "Value": datetime.now(timezone.utc).isoformat()}
            ]
        )
        logger.info(f"Tagged {instance_id} with review-needed=true")
        tagged.append(instance_id)

    return {
        "status": "actioned",
        "resource_id": ", ".join(tagged),
        "resource_type": "EC2",
        "action": "create_tags",
        "rollback_command": "aws ec2 delete-tags --resources <id> --tags Key=review-needed",
        "estimated_saving_usd": 0
    }


ACTION_FUNCTIONS = {
    "stop_ec2_instance":      stop_ec2_instance,
    "cap_lambda_concurrency": cap_lambda_concurrency,
    "tag_resource_for_review": tag_resource_for_review,
}


# ─────────────────────────────────────────
# CIRCUIT BREAKER
# ─────────────────────────────────────────
def check_circuit_breaker(actions_taken):
    """Prevents engine from executing too many actions in one run."""
    if actions_taken >= MAX_ACTIONS_PER_HOUR:
        logger.warning(
            f"Circuit breaker triggered — max {MAX_ACTIONS_PER_HOUR} actions reached. "
            "No further actions will be taken this run."
        )
        return True
    return False


# ─────────────────────────────────────────
# WRITE AUDIT RECORD
# ─────────────────────────────────────────
def write_audit_record(anomaly, action_result):
    """Logs every action (or skip) to OptimizationAudit table and updates AnomalyEvents."""
    action_id = f"action-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    audit_table = get_table(AUDIT_TABLE_NAME)
    anomaly_table = get_table(ANOMALY_TABLE_NAME)

    try:
        audit_table.put_item(Item={
            "action_id": action_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "anomaly_id": anomaly.get("anomaly_id", "unknown"),
            "anomaly_type": anomaly.get("anomaly_type", "unknown"),
            "model": anomaly.get("model", "unknown"),
            "confidence": str(anomaly.get("confidence", 0)),
            "action_taken": action_result.get("action", "none"),
            "resource_id": action_result.get("resource_id", "none"),
            "resource_type": action_result.get("resource_type", "none"),
            "status": action_result.get("status", "unknown"),
            "reason": action_result.get("reason", ""),
            "rollback_command": action_result.get("rollback_command", ""),
            "estimated_saving_usd": str(action_result.get("estimated_saving_usd", 0))
        })
    except ClientError as e:
        logger.error(f"Failed to write audit record to '{AUDIT_TABLE_NAME}': {e}")

    try:
        if "anomaly_id" in anomaly and "timestamp" in anomaly:
            anomaly_table.update_item(
                Key={
                    "anomaly_id": anomaly["anomaly_id"],
                    "timestamp": anomaly["timestamp"]
                },
                UpdateExpression="SET #s = :s, action_taken = :a",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":s": action_result.get("status", "unknown"),
                    ":a": action_result.get("action", "none")
                }
            )
    except ClientError as e:
        logger.error(f"Failed to update anomaly status in '{ANOMALY_TABLE_NAME}': {e}")

    return action_id


# ─────────────────────────────────────────
# MAIN ENGINE RUN
# ─────────────────────────────────────────
def run_engine():
    start_time = datetime.now(timezone.utc).isoformat()
    logger.info(f"Optimization engine started at {start_time}")
    logger.info(f"Circuit breaker limit: {MAX_ACTIONS_PER_HOUR} actions/run")

    anomalies = fetch_pending_anomalies()

    if not anomalies:
        logger.info("No pending anomalies — nothing to action.")
        return

    actions_taken = 0
    total_saving = 0.0

    for anomaly in anomalies:
        if check_circuit_breaker(actions_taken):
            break

        anomaly_type = anomaly.get("anomaly_type", "unknown")
        confidence = safe_float(anomaly.get("confidence"), 0.0)

        logger.info(f"Processing: {anomaly_type} (confidence: {confidence})")
        action_name = ACTION_RULES.get(anomaly_type)

        if not action_name:
            logger.warning(f"No rule defined for anomaly type '{anomaly_type}' — skipping")
            write_audit_record(anomaly, {
                "status": "skipped",
                "reason": f"No rule defined for {anomaly_type}",
                "estimated_saving_usd": 0
            })
            continue

        logger.info(f"Rule matched: {anomaly_type} → {action_name}")

        try:
            result = ACTION_FUNCTIONS[action_name](anomaly)
        except Exception as e:
            result = {
                "status": "failed",
                "reason": str(e),
                "estimated_saving_usd": 0
            }
            logger.error(f"Action failed: {e}")

        action_id = write_audit_record(anomaly, result)

        if result["status"] == "actioned":
            actions_taken += 1
            saving = safe_float(result.get("estimated_saving_usd"), 0.0)
            total_saving += saving
            logger.info(f"Audit record written: {action_id}")
            logger.info(f"Estimated saving: ${saving:.4f}/hr")
            if result.get("rollback_command"):
                logger.info(f"Rollback command: {result['rollback_command']}")

    logger.info("=" * 50)
    logger.info("Optimization Engine Summary:")
    logger.info(f"  Anomalies processed: {len(anomalies)}")
    logger.info(f"  Actions executed:    {actions_taken}")
    logger.info(f"  Actions skipped:     {len(anomalies) - actions_taken}")
    logger.info(f"  Estimated savings:   ${total_saving:.4f}/hr")
    logger.info("=" * 50)


if __name__ == "__main__":
    run_engine()
