import os
import boto3
from dotenv import load_dotenv

load_dotenv()

REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT_URL", None)


def is_simulation_mode() -> bool:
    """Returns True if the system is running in zero-cost simulation mode."""
    return os.getenv("SIMULATION_MODE", "true").lower() in ("true", "1", "yes")


# Backwards-compatible flag
SIMULATION_MODE = is_simulation_mode()


def get_dynamodb_resource():
    """Return a boto3 DynamoDB ServiceResource, honoring DYNAMODB_ENDPOINT_URL or simulation mode."""
    if is_simulation_mode():
        from src.data_store import get_simulated_dynamodb_resource
        return get_simulated_dynamodb_resource()

    kwargs = {"region_name": REGION}
    if DYNAMODB_ENDPOINT_URL:
        kwargs["endpoint_url"] = DYNAMODB_ENDPOINT_URL
    return boto3.resource("dynamodb", **kwargs)


def get_dynamodb_client():
    """Return a boto3 DynamoDB low-level client, honoring DYNAMODB_ENDPOINT_URL or simulation mode."""
    if is_simulation_mode():
        from src.data_store import get_simulated_dynamodb_client
        return get_simulated_dynamodb_client()

    kwargs = {"region_name": REGION}
    if DYNAMODB_ENDPOINT_URL:
        kwargs["endpoint_url"] = DYNAMODB_ENDPOINT_URL
    return boto3.client("dynamodb", **kwargs)


def get_table(table_name):
    """Return a boto3 DynamoDB Table instance (or InMemoryTable in simulation mode)."""
    if is_simulation_mode():
        from src.data_store import get_in_memory_table
        from src.synthetic_data import seed_historical_data
        seed_historical_data()
        return get_in_memory_table(table_name)

    return get_dynamodb_resource().Table(table_name)


def get_ec2_client():
    if is_simulation_mode():
        from src.data_store import get_simulated_ec2_client
        return get_simulated_ec2_client()
    return boto3.client("ec2", region_name=REGION)


def get_lambda_client():
    if is_simulation_mode():
        from src.data_store import get_simulated_lambda_client
        return get_simulated_lambda_client()
    return boto3.client("lambda", region_name=REGION)


def get_cloudwatch_client():
    if is_simulation_mode():
        from src.data_store import get_simulated_cloudwatch_client
        return get_simulated_cloudwatch_client()
    return boto3.client("cloudwatch", region_name=REGION)


def get_s3_client():
    if is_simulation_mode():
        from src.data_store import get_simulated_s3_client
        return get_simulated_s3_client()
    return boto3.client("s3", region_name=REGION)


def get_sts_client():
    if is_simulation_mode():
        from src.data_store import get_simulated_sts_client
        return get_simulated_sts_client()
    return boto3.client("sts", region_name=REGION)
