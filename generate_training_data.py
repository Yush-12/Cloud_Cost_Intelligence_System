import boto3
import os
import random
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

REGION = os.getenv("AWS_REGION", "us-east-1")
TABLE_NAME = os.getenv("DYNAMODB_TABLE", "CostTelemetry")

dynamo = boto3.resource('dynamodb', region_name=REGION)
table = dynamo.Table(TABLE_NAME)

def generate_training_data(days=5):
    """
    Generates 5 days of synthetic metrics simulating:
    - Normal EC2 CPU: 2-15% during day, 0.5-3% at night
    - 3 injected anomalies: CPU spike, cost spike, idle instance
    """
    print(f"Generating {days} days of synthetic training data...")
    
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

            # Inject anomalies on day 2 at 3AM (idle spike)
            # and day 3 at 2PM (CPU runaway)
            if day_offset == 3 and hour == 3:
                base_cpu = random.uniform(0.1, 0.3)   # Anomaly: suspiciously idle
            if day_offset == 2 and hour == 14:
                base_cpu = random.uniform(85, 95)      # Anomaly: CPU runaway

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
                # Inject cost spike on day 2 at 2PM
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

    print(f"✅ Written {records_written} synthetic training records to DynamoDB")
    print("   Anomalies injected:")
    print("   - Day 3 03:00 UTC → Idle CPU spike (0.1-0.3%)")
    print("   - Day 2 14:00 UTC → CPU runaway (85-95%)")
    print("   - Day 2 14:00 UTC → Cost spike ($2.50)")

if __name__ == "__main__":
    generate_training_data(days=5)