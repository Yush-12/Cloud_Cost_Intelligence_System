"""
src/aws_clients.py — Simulation-only AWS client provider for Cloud Cost Intelligence System.
All calls route directly to the thread-safe in-memory simulation engine.
No boto3 network connections or real AWS credentials are ever used.
"""

import os
from dotenv import load_dotenv

from src.data_store import (
    get_in_memory_table,
    get_simulated_dynamodb_resource,
    get_simulated_dynamodb_client,
    get_simulated_ec2_client,
    get_simulated_lambda_client,
    get_simulated_cloudwatch_client,
    get_simulated_s3_client,
    get_simulated_sts_client,
)
from src.synthetic_data import seed_historical_data

load_dotenv()

REGION = os.getenv("AWS_REGION", "us-east-1")


def get_dynamodb_resource():
    """Return the simulated DynamoDB ServiceResource."""
    return get_simulated_dynamodb_resource()


def get_dynamodb_client():
    """Return the simulated DynamoDB low-level client."""
    return get_simulated_dynamodb_client()


def get_table(table_name):
    """Return the simulated in-memory DynamoDB Table, seeding data on first access."""
    seed_historical_data()
    return get_in_memory_table(table_name)


def get_ec2_client():
    """Return the simulated EC2 client."""
    return get_simulated_ec2_client()


def get_lambda_client():
    """Return the simulated Lambda client."""
    return get_simulated_lambda_client()


def get_cloudwatch_client():
    """Return the simulated CloudWatch client."""
    return get_simulated_cloudwatch_client()


def get_s3_client():
    """Return the simulated S3 client."""
    return get_simulated_s3_client()


def get_sts_client():
    """Return the simulated STS client."""
    return get_simulated_sts_client()
