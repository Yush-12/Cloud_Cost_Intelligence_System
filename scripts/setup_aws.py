#!/usr/bin/env python3
"""
setup_aws.py — Idempotent setup script for Cloud Cost Intelligence System.
Verifies AWS credentials / DynamoDB connectivity and creates required DynamoDB tables:
  1. CostTelemetry
  2. AnomalyEvents
  3. OptimizationAudit
"""

import os
import sys
import logging
from pathlib import Path
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

# Ensure repository root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.aws_clients import (
    get_dynamodb_resource,
    get_dynamodb_client,
    get_sts_client,
    REGION,
    DYNAMODB_ENDPOINT_URL,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("setup_aws")

TABLE_SCHEMAS = {
    os.getenv("DYNAMODB_TABLE", "CostTelemetry"): {
        "KeySchema": [
            {"AttributeName": "resource_id", "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"}
        ],
        "AttributeDefinitions": [
            {"AttributeName": "resource_id", "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "S"}
        ]
    },
    os.getenv("ANOMALY_TABLE", "AnomalyEvents"): {
        "KeySchema": [
            {"AttributeName": "anomaly_id", "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"}
        ],
        "AttributeDefinitions": [
            {"AttributeName": "anomaly_id", "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "S"}
        ]
    },
    os.getenv("AUDIT_TABLE", "OptimizationAudit"): {
        "KeySchema": [
            {"AttributeName": "action_id", "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"}
        ],
        "AttributeDefinitions": [
            {"AttributeName": "action_id", "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "S"}
        ]
    }
}


def verify_connection():
    """Verify AWS credentials or local DynamoDB endpoint connectivity."""
    if DYNAMODB_ENDPOINT_URL:
        logger.info(f"Targeting custom DynamoDB endpoint: {DYNAMODB_ENDPOINT_URL}")
        try:
            client = get_dynamodb_client()
            client.list_tables()
            logger.info("✅ Connected successfully to DynamoDB endpoint.")
            return True
        except (EndpointConnectionError, ClientError) as e:
            logger.error(f"❌ Could not connect to DynamoDB endpoint at {DYNAMODB_ENDPOINT_URL}: {e}")
            logger.error("   Make sure DynamoDB Local is running (e.g., via docker compose up).")
            return False

    logger.info(f"Targeting AWS Region: {REGION}")
    try:
        sts = get_sts_client()
        identity = sts.get_caller_identity()
        logger.info(f"✅ AWS Credentials verified. Account: {identity.get('Account')}, ARN: {identity.get('Arn')}")
        return True
    except NoCredentialsError:
        logger.error("❌ AWS credentials not found.")
        logger.error("   Please set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env or run 'aws configure'.")
        return False
    except ClientError as e:
        logger.error(f"❌ AWS credential verification failed: {e}")
        return False


def setup_tables():
    """Ensure all required DynamoDB tables exist and are active."""
    dynamo = get_dynamodb_resource()
    client = get_dynamodb_client()

    try:
        existing_tables = client.list_tables().get("TableNames", [])
    except Exception as e:
        logger.error(f"❌ Failed to list DynamoDB tables: {e}")
        return False

    all_success = True

    for table_name, schema in TABLE_SCHEMAS.items():
        if table_name in existing_tables:
            logger.info(f"  ✓ Table '{table_name}' already exists.")
            continue

        logger.info(f"  Creating table '{table_name}'...")
        try:
            table = dynamo.create_table(
                TableName=table_name,
                KeySchema=schema["KeySchema"],
                AttributeDefinitions=schema["AttributeDefinitions"],
                BillingMode="PAY_PER_REQUEST"
            )
            logger.info(f"    ⏳ Waiting for '{table_name}' to become active...")
            table.wait_until_exists()
            logger.info(f"  ✅ Table '{table_name}' is active and ready.")
        except ClientError as e:
            logger.error(f"  ❌ Failed to create table '{table_name}': {e}")
            all_success = False

    return all_success


def main():
    logger.info("=" * 60)
    logger.info("Cloud Cost Intelligence System — AWS & DynamoDB Setup")
    logger.info("=" * 60)

    if not verify_connection():
        sys.exit(1)

    logger.info("\nChecking and creating required DynamoDB tables...")
    success = setup_tables()

    if success:
        logger.info("\n🎉 All DynamoDB tables are verified and ready to use!")
        logger.info("Next step: run 'python app.py'.\n")
        sys.exit(0)
    else:
        logger.error("\n❌ Setup completed with errors. Check the logs above.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
