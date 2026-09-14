import os
import random
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from botocore.exceptions import ClientError

from aws_clients import get_table, REGION

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("generate_training_data")

TABLE_NAME = os.getenv("DYNAMODB_TABLE", "CostTelemetry")


def generate_training_data(days=5):
    """
    Generates synthetic metrics simulating:
    - Normal EC2 CPU: 5-15% during day, 0.5-3% at night
    - Injected anomalies: CPU spike, cost spike, idle instance
    """
    logger.info(f"Generating {days} days of synthetic training data...")

    try:
        table = get_table(TABLE_NAME)
        table.load()
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            logger.error(f"Table '{TABLE_NAME}' does not exist! Run 'python setup_aws.py' first.")
            return 0
        logger.error(f"Error accessing table '{TABLE_NAME}': {e}")
        return 0

    EC2_INSTANCE_ID = os.getenv("EC2_INSTANCE_ID", "i-0aa9b48a77f3f6bd7")
    now = datetime.now(timezone.utc)
    records_written = 0

    for day_offset in range(days, 0, -1):
        for hour in range(24):
            timestamp = now - timedelta(days=day_offset, hours=-hour)
            ts_str = timestamp.isoformat()

            # Normal CPU pattern: higher during day, low at night
            is_daytime = 8 <= hour <= 20
            base_cpu = random.uniform(5, 15) if is_daytime else random.uniform(0.5, 3)

            # Inject anomalies on day 3 at 3AM (idle spike)
            # and day 2 at 2PM (CPU runaway)
            if day_offset == 3 and hour == 3:
                base_cpu = random.uniform(0.1, 0.3)
            if day_offset == 2 and hour == 14:
                base_cpu = random.uniform(85, 95)

            # Write utilization record
            table.put_item(Item={
                "resource_id": EC2_INSTANCE_ID,
                "timestamp": ts_str,
                "metric_type": "utilization",
                "service": "Amazon EC2",
                "region": REGION,
                "cpu_utilization": str(round(base_cpu, 4))
            })

            # Write billing record every 6 hours
            if hour % 6 == 0:
                cost = 2.50 if (day_offset == 2 and hour == 14) else round(random.uniform(0.01, 0.05), 4)
                table.put_item(Item={
                    "resource_id": EC2_INSTANCE_ID,
                    "timestamp": ts_str,
                    "metric_type": "billing",
                    "service": "Amazon EC2",
                    "region": REGION,
                    "cost_usd": str(cost)
                })

            records_written += 1

    logger.info(f"Written {records_written} synthetic training records to DynamoDB table '{TABLE_NAME}'")
    logger.info("Anomalies injected:")
    logger.info("  - Day 3 03:00 UTC → Idle CPU (0.1-0.3%)")
    logger.info("  - Day 2 14:00 UTC → CPU runaway (85-95%)")
    logger.info("  - Day 2 14:00 UTC → Cost spike ($2.50)")
    return records_written


if __name__ == "__main__":
    generate_training_data(days=5)