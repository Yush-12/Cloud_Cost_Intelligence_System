#!/usr/bin/env python3
"""
rollback.py — Reverses optimization actions taken by the Cloud Cost Intelligence System.
Queries OptimizationAudit table to target only resources altered by the system.
Supports --dry-run and fallback --all-resources.
"""

import os
import sys
import logging
import argparse
from dotenv import load_dotenv
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

from db_utils import scan_all
from aws_clients import (
    get_table,
    get_ec2_client,
    get_lambda_client
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("rollback")

AUDIT_TABLE_NAME = os.getenv("AUDIT_TABLE", "OptimizationAudit")


def rollback_audit_actions(dry_run=False):
    """Roll back only the actions recorded in OptimizationAudit."""
    logger.info("Querying OptimizationAudit for actioned optimizations...")
    try:
        audit_table = get_table(AUDIT_TABLE_NAME)
        items = scan_all(audit_table, Attr("status").eq("actioned"))
    except ClientError as e:
        logger.error(f"Failed to access '{AUDIT_TABLE_NAME}': {e}")
        return

    if not items:
        logger.info("No active 'actioned' records found to roll back.")
        return

    logger.info(f"Found {len(items)} actioned record(s) to process.")
    ec2_client = get_ec2_client()
    lambda_client = get_lambda_client()

    for item in items:
        action_id = item.get("action_id")
        action_taken = item.get("action_taken", "")
        resource_id = item.get("resource_id", "")
        resource_type = item.get("resource_type", "")
        timestamp = item.get("timestamp")

        logger.info(f"\nProcessing action {action_id} on {resource_type} ({resource_id}): {action_taken}")

        if dry_run:
            logger.info(f"  [DRY RUN] Would rollback: {action_taken} on {resource_id}")
            continue

        try:
            if action_taken == "stop_instances" and resource_id and resource_id != "none":
                logger.info(f"  Restarting EC2 instance: {resource_id}")
                ec2_client.start_instances(InstanceIds=[resource_id])
                logger.info(f"  ✅ Restarted EC2 instance {resource_id}")

            elif action_taken == "put_function_concurrency" and resource_id and resource_id != "none":
                func_names = [f.strip() for f in resource_id.split(",") if f.strip()]
                for func_name in func_names:
                    logger.info(f"  Removing concurrency cap from Lambda: {func_name}")
                    lambda_client.delete_function_concurrency(FunctionName=func_name)
                    logger.info(f"  ✅ Removed concurrency cap from {func_name}")

            elif action_taken == "create_tags" and resource_id and resource_id != "none":
                res_ids = [r.strip() for r in resource_id.split(",") if r.strip()]
                for res_id in res_ids:
                    logger.info(f"  Removing review-needed tag from {res_id}")
                    ec2_client.delete_tags(
                        Resources=[res_id],
                        Tags=[{"Key": "review-needed"}]
                    )
                    logger.info(f"  ✅ Removed tag from {res_id}")

            elif action_taken == "flagged_for_review":
                logger.info(f"  No infrastructure changes were made for flag: {resource_id}")

            else:
                logger.warning(f"  Unknown or unhandled action type '{action_taken}', skipping infrastructure change.")

            # Update audit status to rolled_back
            if timestamp:
                audit_table.update_item(
                    Key={"action_id": action_id, "timestamp": timestamp},
                    UpdateExpression="SET #s = :s",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":s": "rolled_back"}
                )
                logger.info(f"  Status updated to 'rolled_back' for {action_id}")

        except ClientError as e:
            logger.error(f"  ❌ Error rolling back action {action_id}: {e}")

    logger.info("\nAudit-based rollback completed.")


def rollback_all_resources(dry_run=False):
    """Fallback: Restart ALL stopped EC2 instances and remove ALL Lambda concurrency caps."""
    logger.warning("⚠️  Running scorched-earth rollback on ALL account resources!")
    ec2_client = get_ec2_client()
    lambda_client = get_lambda_client()

    # Restart stopped EC2 instances
    try:
        response = ec2_client.describe_instances(
            Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}]
        )
        stopped_ids = [
            i["InstanceId"] for r in response.get("Reservations", []) for i in r.get("Instances", [])
        ]
        if stopped_ids:
            logger.info(f"Found {len(stopped_ids)} stopped EC2 instance(s): {', '.join(stopped_ids)}")
            if not dry_run:
                ec2_client.start_instances(InstanceIds=stopped_ids)
                logger.info("✅ All stopped EC2 instances restarted.")
            else:
                logger.info("[DRY RUN] Would restart these instances.")
        else:
            logger.info("No stopped EC2 instances found.")
    except ClientError as e:
        logger.error(f"Error checking/starting EC2 instances: {e}")

    # Remove all Lambda concurrency caps
    try:
        response = lambda_client.list_functions()
        functions = response.get("Functions", [])
        for func in functions:
            name = func["FunctionName"]
            if dry_run:
                logger.info(f"[DRY RUN] Would delete function concurrency on {name}")
            else:
                try:
                    lambda_client.delete_function_concurrency(FunctionName=name)
                    logger.info(f"✅ Removed concurrency cap from Lambda: {name}")
                except ClientError as e:
                    # Function may not have reserved concurrency set
                    if e.response["Error"]["Code"] != "ResourceNotFoundException":
                        logger.warning(f"Note for {name}: {e}")
    except ClientError as e:
        logger.error(f"Error checking/modifying Lambda functions: {e}")

    logger.info("Account-wide rollback completed.")


def main():
    parser = argparse.ArgumentParser(description="Cloud Cost Intelligence Rollback")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what actions would be rolled back without making changes"
    )
    parser.add_argument(
        "--all-resources",
        action="store_true",
        help="Fallback: restart ALL stopped EC2 instances and remove ALL Lambda caps in account"
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("↩️  Cloud Cost Intelligence System — Rollback")
    if args.dry_run:
        logger.info("MODE: DRY RUN (no actions will be executed)")
    logger.info("=" * 60)

    if args.all_resources:
        rollback_all_resources(dry_run=args.dry_run)
    else:
        rollback_audit_actions(dry_run=args.dry_run)


if __name__ == "__main__":
    main()