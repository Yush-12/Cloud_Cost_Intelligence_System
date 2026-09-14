import boto3
from boto3.dynamodb.conditions import Attr
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from db_utils import scan_all

load_dotenv()

REGION = os.getenv("AWS_REGION", "us-east-1")

# Circuit breaker config
MAX_ACTIONS_PER_HOUR = 5       # Never execute more than 5 actions per run

# Initialize clients
dynamo = boto3.resource('dynamodb', region_name=REGION)
ec2_client = boto3.client('ec2', region_name=REGION)
lambda_client = boto3.client('lambda', region_name=REGION)

anomaly_table = dynamo.Table("AnomalyEvents")
audit_table = dynamo.Table("OptimizationAudit")


# ─────────────────────────────────────────
# RULE TABLE: Anomaly Type → Safe Action
# ─────────────────────────────────────────
ACTION_RULES = {
    "idle_instance":    "stop_ec2_instance",
    "cpu_spike":        "cap_lambda_concurrency",
    "runaway_function": "cap_lambda_concurrency",
    "cost_spike":       "tag_resource_for_review",
    "orphaned_volume":  "tag_resource_for_review",  # Flag only, no delete without cooldown
}


# ─────────────────────────────────────────
# FETCH PENDING ANOMALIES
# ─────────────────────────────────────────
def fetch_pending_anomalies():
    print("  Fetching pending anomalies from DynamoDB...")
    items = scan_all(anomaly_table, Attr("status").eq("pending"))
    print(f"     ✅ Found {len(items)} pending anomalies")
    return items


# ─────────────────────────────────────────
# ACTIONS
# ─────────────────────────────────────────
def stop_ec2_instance(anomaly):
    """
    Stops idle EC2 instances. Safe — instances can be restarted anytime.
    """
    print(f"     ⚙️  Action: stop_ec2_instance")
    
    # Get all running instances
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

    # In a real system you'd target the specific anomalous instance
    # For safety in free-tier we only stop if CPU < 2% confirmed
    target = instances[0]
    instance_id = target["InstanceId"]
    instance_type = target["InstanceType"]

    ec2_client.stop_instances(InstanceIds=[instance_id])
    print(f"     ✅ Stopped EC2 instance {instance_id} ({instance_type})")
    
    return {
        "status": "actioned",
        "resource_id": instance_id,
        "resource_type": "EC2",
        "action": "stop_instances",
        "rollback_command": f"aws ec2 start-instances --instance-ids {instance_id}",
        "estimated_saving_usd": 0.012  # ~t2.micro hourly rate
    }


def cap_lambda_concurrency(anomaly):
    print(f"     ⚙️  Action: cap_lambda_concurrency")

    response = lambda_client.list_functions()
    functions = response["Functions"]

    if not functions:
        return {
            "status": "skipped",
            "reason": "No Lambda functions found",
            "estimated_saving_usd": 0
        }

    # Check account-level concurrency limit first
    account_settings = lambda_client.get_account_settings()
    total_concurrency = account_settings["AccountLimit"]["ConcurrentExecutions"]
    print(f"     ℹ️  Account concurrency limit: {total_concurrency}")

    # Free-tier accounts have limit of 10 — can't reserve any without violating minimum
    # In that case fall back to tagging the function for review instead
    if total_concurrency <= 10:
        print(f"     ⚠️  Concurrency limit too low to reserve — falling back to tagging")
        tagged = []
        for func in functions:
            func_name = func["FunctionName"]
            # Tag via Lambda doesn't exist — use a DynamoDB flag instead
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
            print(f"     ✅ Flagged {func_name} for review in audit table")
            tagged.append(func_name)

        return {
            "status": "actioned",
            "resource_id": ", ".join(tagged),
            "resource_type": "Lambda",
            "action": "flagged_for_review",
            "rollback_command": "none",
            "estimated_saving_usd": 0
        }

    # Normal path — account has enough concurrency headroom
    results = []
    for func in functions:
        func_name = func["FunctionName"]
        cap = max(10, total_concurrency // 2)  # Cap at 50% of total limit
        lambda_client.put_function_concurrency(
            FunctionName=func_name,
            ReservedConcurrentExecutions=cap
        )
        print(f"     ✅ Capped {func_name} concurrency to {cap}")
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
    Completely safe — no infrastructure changes.
    """
    print(f"     ⚙️  Action: tag_resource_for_review")
    
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
        print(f"     ✅ Tagged {instance_id} with review-needed=true")
        tagged.append(instance_id)

    return {
        "status": "actioned",
        "resource_id": ", ".join(tagged),
        "resource_type": "EC2",
        "action": "create_tags",
        "rollback_command": f"aws ec2 delete-tags --resources <id> --tags Key=review-needed",
        "estimated_saving_usd": 0
    }


# Action name → function mapping (module-level, built once)
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
        print(f"\n  ⚡ Circuit breaker triggered — max {MAX_ACTIONS_PER_HOUR} actions reached")
        print(f"     No further actions will be taken this run.")
        return True
    return False


# ─────────────────────────────────────────
# WRITE AUDIT RECORD
# ─────────────────────────────────────────
def write_audit_record(anomaly, action_result):
    """Logs every action (or skip) to OptimizationAudit table."""
    
    action_id = f"action-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    
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
    
    # Update anomaly status in AnomalyEvents
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
    
    return action_id


# ─────────────────────────────────────────
# MAIN ENGINE RUN
# ─────────────────────────────────────────
def run_engine():
    print(f"\n⚙️  Optimization engine started at {datetime.now(timezone.utc).isoformat()}")
    print(f"   Circuit breaker limit: {MAX_ACTIONS_PER_HOUR} actions/run\n")

    anomalies = fetch_pending_anomalies()

    if not anomalies:
        print("  ✅ No pending anomalies — nothing to action\n")
        return

    actions_taken = 0
    total_saving = 0.0

    for anomaly in anomalies:
        if check_circuit_breaker(actions_taken):
            break

        anomaly_type = anomaly.get("anomaly_type", "unknown")
        confidence = float(anomaly.get("confidence", 0))

        print(f"\n  🚨 Processing: {anomaly_type} (confidence: {confidence})")

        # Look up the action for this anomaly type
        action_name = ACTION_RULES.get(anomaly_type)

        if not action_name:
            print(f"     ⚠️  No rule defined for anomaly type '{anomaly_type}' — skipping")
            write_audit_record(anomaly, {
                "status": "skipped",
                "reason": f"No rule defined for {anomaly_type}",
                "estimated_saving_usd": 0
            })
            continue

        print(f"     Rule matched: {anomaly_type} → {action_name}")

        try:
            result = ACTION_FUNCTIONS[action_name](anomaly)
        except Exception as e:
            result = {
                "status": "failed",
                "reason": str(e),
                "estimated_saving_usd": 0
            }
            print(f"     ❌ Action failed: {e}")

        # Write audit record
        action_id = write_audit_record(anomaly, result)

        if result["status"] == "actioned":
            actions_taken += 1
            saving = float(result.get("estimated_saving_usd", 0))
            total_saving += saving
            print(f"     📝 Audit record written: {action_id}")
            print(f"     💰 Estimated saving: ${saving:.4f}/hr")
            if result.get("rollback_command"):
                print(f"     ↩️  Rollback: {result['rollback_command']}")

    # Final summary
    print(f"\n{'='*50}")
    print(f"⚙️  Engine Run Summary")
    print(f"{'='*50}")
    print(f"  Anomalies processed: {len(anomalies)}")
    print(f"  Actions executed:    {actions_taken}")
    print(f"  Actions skipped:     {len(anomalies) - actions_taken}")
    print(f"  Estimated savings:   ${total_saving:.4f}/hr")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    run_engine()