import boto3
from boto3.dynamodb.conditions import Attr
import os
import statistics
from datetime import datetime, timezone
from dotenv import load_dotenv
from db_utils import scan_all

load_dotenv()

REGION = os.getenv("AWS_REGION", "us-east-1")
TABLE_NAME = os.getenv("DYNAMODB_TABLE", "CostTelemetry")
CONFIDENCE_THRESHOLD = 0.85  # Anomalies above this score get escalated

dynamo = boto3.resource('dynamodb', region_name=REGION)
table = dynamo.Table(TABLE_NAME)


def safe_float(val, default=0.0):
    """Safely cast a value to float, handling None, empty string, and exceptions."""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ─────────────────────────────────────────
# STEP 1: Fetch Data from DynamoDB
# ─────────────────────────────────────────
def fetch_metrics(metric_type="utilization", resource_id=None):
    """Fetch all records of a given metric type from DynamoDB."""
    print(f"  Fetching {metric_type} records from DynamoDB...")
    filter_expr = Attr("metric_type").eq(metric_type)
    if resource_id:
        filter_expr = filter_expr & Attr("resource_id").eq(resource_id)
    items = scan_all(table, filter_expr)
    print(f"     ✅ Fetched {len(items)} records")
    return items


# ─────────────────────────────────────────
# STEP 2: Statistical Anomaly Detection (Time-Series)
# ─────────────────────────────────────────
def detect_time_series_anomalies(items):
    """
    Detects time-series CPU anomalies using moving baseline statistics.
    Identifies runaway spikes and idle instances with high confidence.
    """
    print("\n  [DETECTOR 1] Running statistical time-series detection...")
    
    if not items:
        print("     ⚠️  No utilization data available — skipping")
        return []

    # Sort items chronologically
    sorted_items = sorted(items, key=lambda x: x.get("timestamp", ""))
    cpu_values = [safe_float(item.get("cpu_utilization"), 0.0) for item in sorted_items]

    mean_cpu = statistics.mean(cpu_values)
    stdev_cpu = statistics.stdev(cpu_values) if len(cpu_values) > 1 else 1.0

    results = []
    for item in sorted_items:
        cpu = safe_float(item.get("cpu_utilization"), 0.0)
        ts = item.get("timestamp", datetime.now(timezone.utc).isoformat())

        if cpu >= 80.0 or (stdev_cpu > 0 and (cpu - mean_cpu) / stdev_cpu >= 2.5):
            excess = max(0.0, cpu - 80.0)
            confidence = min(0.99, 0.85 + (excess / 20.0) * 0.14)
            results.append({
                "timestamp": ts,
                "model": "statistical_time_series",
                "anomaly_type": "cpu_spike",
                "actual_value": round(cpu, 4),
                "expected_value": round(mean_cpu, 4),
                "confidence": round(confidence, 4)
            })
        elif cpu <= 1.0 or (stdev_cpu > 0 and (mean_cpu - cpu) / stdev_cpu >= 2.5):
            idle_ratio = max(0.0, 1.0 - min(cpu, 1.0))
            confidence = min(0.99, 0.85 + idle_ratio * 0.14)
            results.append({
                "timestamp": ts,
                "model": "statistical_time_series",
                "anomaly_type": "idle_instance",
                "actual_value": round(cpu, 4),
                "expected_value": round(mean_cpu, 4),
                "confidence": round(confidence, 4)
            })

    print(f"     ✅ Found {len(results)} time-series anomalies")
    return results


# ─────────────────────────────────────────
# STEP 3: Multivariate Anomaly Detection
# ─────────────────────────────────────────
def detect_multivariate_anomalies(utilization_items, billing_items):
    """
    Detects multivariate anomalies correlating cost and CPU usage.
    Catches runaway functions, orphaned volumes, and cost spikes.
    """
    print("\n  [DETECTOR 2] Running multivariate correlation detection...")

    # Bucket timestamps to hour strings (YYYY-MM-DDTHH)
    util_by_hour = {}
    for item in utilization_items:
        ts = item.get("timestamp", "")
        hour_key = ts[:13] if len(ts) >= 13 else ts
        util_by_hour.setdefault(hour_key, []).append(safe_float(item.get("cpu_utilization"), 0.0))

    bill_by_hour = {}
    for item in billing_items:
        ts = item.get("timestamp", "")
        hour_key = ts[:13] if len(ts) >= 13 else ts
        bill_by_hour.setdefault(hour_key, []).append(safe_float(item.get("cost_usd"), 0.0))

    common_hours = set(util_by_hour.keys()) & set(bill_by_hour.keys())
    if not common_hours:
        # If timestamps don't align on exact hour, examine latest billing points directly
        if billing_items:
            results = []
            for b in billing_items:
                cost = safe_float(b.get("cost_usd"), 0.0)
                if cost > 1.0:
                    results.append({
                        "timestamp": b.get("timestamp", datetime.now(timezone.utc).isoformat()),
                        "model": "multivariate_rules",
                        "anomaly_type": "cost_spike",
                        "cost_value": round(cost, 4),
                        "confidence": 0.95
                    })
            return results
        return []

    results = []
    for hour_key in sorted(common_hours):
        avg_cpu = statistics.mean(util_by_hour[hour_key])
        avg_cost = statistics.mean(bill_by_hour[hour_key])

        anomaly_type = None
        confidence = 0.85

        if avg_cpu > 70 and avg_cost > 0.5:
            anomaly_type = "runaway_function"
            confidence = 0.95
        elif avg_cpu < 2 and avg_cost > 0.1:
            anomaly_type = "orphaned_volume"
            confidence = 0.90
        elif avg_cost > 1.0:
            anomaly_type = "cost_spike"
            confidence = 0.92
        elif avg_cpu < 1.0:
            anomaly_type = "idle_instance"
            confidence = 0.88

        if anomaly_type:
            results.append({
                "timestamp": f"{hour_key}:00:00Z",
                "model": "multivariate_rules",
                "anomaly_type": anomaly_type,
                "cpu_value": round(avg_cpu, 4),
                "cost_value": round(avg_cost, 4),
                "confidence": round(confidence, 4)
            })

    print(f"     ✅ Found {len(results)} multivariate anomalies")
    return results


# ─────────────────────────────────────────
# STEP 4: Write Anomalies to DynamoDB
# ─────────────────────────────────────────
def save_anomalies(anomalies):
    """Saves detected anomalies to DynamoDB for optimization engine to consume."""
    try:
        anomaly_table = dynamo.Table("AnomalyEvents")
        anomaly_table.load()
    except Exception:
        existing = [t.name for t in dynamo.tables.all()]
        if "AnomalyEvents" not in existing:
            print("\n  Creating AnomalyEvents table...")
            new_table = dynamo.create_table(
                TableName="AnomalyEvents",
                KeySchema=[
                    {"AttributeName": "anomaly_id", "KeyType": "HASH"},
                    {"AttributeName": "timestamp", "KeyType": "RANGE"}
                ],
                AttributeDefinitions=[
                    {"AttributeName": "anomaly_id", "AttributeType": "S"},
                    {"AttributeName": "timestamp", "AttributeType": "S"}
                ],
                BillingMode="PAY_PER_REQUEST"
            )
            print("     ⏳ Waiting for table to become active...")
            new_table.wait_until_exists()
            print("     ✅ AnomalyEvents table ready")
        anomaly_table = dynamo.Table("AnomalyEvents")

    saved = 0
    now_str = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    for i, anomaly in enumerate(anomalies):
        if anomaly["confidence"] >= CONFIDENCE_THRESHOLD:
            anomaly_table.put_item(Item={
                "anomaly_id": f"anomaly-{i}-{now_str}",
                "timestamp": anomaly["timestamp"],
                "model": anomaly["model"],
                "anomaly_type": anomaly["anomaly_type"],
                "confidence": str(anomaly["confidence"]),
                "status": "pending",
                "action_taken": "none"
            })
            saved += 1

    print(f"\n  ✅ Saved {saved} high-confidence anomalies to AnomalyEvents table")
    return saved


# ─────────────────────────────────────────
# MAIN DETECTION RUN
# ─────────────────────────────────────────
def run_detection():
    print(f"\n🔍 Anomaly detection started at {datetime.now(timezone.utc).isoformat()}")
    print(f"   Confidence threshold: {CONFIDENCE_THRESHOLD}\n")

    utilization_items = fetch_metrics("utilization")
    billing_items = fetch_metrics("billing")

    ts_anomalies = detect_time_series_anomalies(utilization_items)
    multi_anomalies = detect_multivariate_anomalies(utilization_items, billing_items)

    all_anomalies = ts_anomalies + multi_anomalies

    print(f"\n📊 Detection Summary:")
    print(f"   Time-series anomalies:  {len(ts_anomalies)}")
    print(f"   Multivariate anomalies: {len(multi_anomalies)}")
    print(f"   Total:                  {len(all_anomalies)}")

    if all_anomalies:
        print(f"\n   High-confidence anomalies (≥{CONFIDENCE_THRESHOLD}):")
        for a in all_anomalies:
            if a["confidence"] >= CONFIDENCE_THRESHOLD:
                print(f"   🚨 [{a['model']}] {a['anomaly_type']} at {a['timestamp']} — confidence: {a['confidence']}")

    save_anomalies(all_anomalies)
    print(f"\n✅ Detection run complete\n")


if __name__ == "__main__":
    run_detection()