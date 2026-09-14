import os
import boto3
from dotenv import load_dotenv

load_dotenv()

REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT_URL", None)


def get_dynamodb_resource():
    """Return a boto3 DynamoDB ServiceResource, honoring DYNAMODB_ENDPOINT_URL if set."""
    kwargs = {"region_name": REGION}
    if DYNAMODB_ENDPOINT_URL:
        kwargs["endpoint_url"] = DYNAMODB_ENDPOINT_URL
    return boto3.resource("dynamodb", **kwargs)


def get_dynamodb_client():
    """Return a boto3 DynamoDB low-level client, honoring DYNAMODB_ENDPOINT_URL if set."""
    kwargs = {"region_name": REGION}
    if DYNAMODB_ENDPOINT_URL:
        kwargs["endpoint_url"] = DYNAMODB_ENDPOINT_URL
    return boto3.client("dynamodb", **kwargs)


def get_table(table_name):
    """Return a boto3 DynamoDB Table instance."""
    return get_dynamodb_resource().Table(table_name)


def get_ec2_client():
    return boto3.client("ec2", region_name=REGION)


def get_lambda_client():
    return boto3.client("lambda", region_name=REGION)


def get_cloudwatch_client():
    return boto3.client("cloudwatch", region_name=REGION)


def get_s3_client():
    return boto3.client("s3", region_name=REGION)


def get_sts_client():
    return boto3.client("sts", region_name=REGION)
