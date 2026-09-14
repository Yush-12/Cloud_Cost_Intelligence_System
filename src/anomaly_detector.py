import os
import sys
import statistics
import logging
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

# Ensure repository root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db_utils import scan_all, safe_float
from src.aws_clients import get_table

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("anomaly_detector")

TABLE_NAME = os.getenv("DYNAMODB_TABLE", "CostTelemetry")
ANOMALY_TABLE_NAME = os.getenv("ANOMALY_TABLE", "AnomalyEvents")
CONFIDENCE_THRESHOLD = safe_float(os.getenv("CONFIDENCE_THRESHOLD"), 0.85)


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
    logger.info(f"Fetching {metric_type} records from DynamoDB table '{TABLE_NAME}'...")
    try:
        table = get_table(TABLE_NAME)
        filter_expr = Attr("metric_type").eq(metric_type)
        if resource_id:
            filter_expr = filter_expr & Attr("resource_id").eq(resource_id)
        items = scan_all(table, filter_expr)
        logger.info(f"Fetched {len(items)} {metric_type} records")
        return items
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            logger.error(
                f"DynamoDB table '{TABLE_NAME}' does not exist! "
                "Please run 'python scripts/setup_aws.py' to initialize the required tables."
            )
        else:
            logger.error(f"Failed to fetch metrics: {e}")
        return []


# ─────────────────────────────────────────
# STEP 2: Statistical Anomaly Detection (Time-Series)
# ─────────────────────────────────────────
def detect_time_series_anomalies(items):
    """
    Detects time-series CPU anomalies using moving baseline statistics.
    Identifies runaway spikes and idle instances with high confidence.
    """
    logger.info("[DETECTOR 1] Running statistical time-series detection...")

    if not items:
        logger.warning("No utilization data available — skipping time-series detection")
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

    logger.info(f"Found {len(results)} time-series anomalies")
    return results


# ─────────────────────────────────────────
# STEP 3: Multivariate Anomaly Detection
# ─────────────────────────────────────────
def detect_multivariate_anomalies(utilization_items, billing_items):
    """
    Detects multivariate anomalies correlating cost and CPU usage.
    Catches runaway functions, orphaned volumes, and cost spikes.
    """
    logger.info("[DETECTOR 2] Running multivariate correlation detection...")

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

    logger.info(f"Found {len(results)} multivariate anomalies")
    return results


# ─────────────────────────────────────────
# STEP 4: Write Anomalies to DynamoDB
# ─────────────────────────────────────────
def save_anomalies(anomalies):
    """Saves detected anomalies to DynamoDB for optimization engine to consume."""
    try:
        anomaly_table = get_table(ANOMALY_TABLE_NAME)
        anomaly_table.load()
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            logger.error(
                f"DynamoDB table '{ANOMALY_TABLE_NAME}' does not exist! "
                "Please run 'python scripts/setup_aws.py' to initialize the required tables."
            )
            return 0
        else:
            logger.error(f"Error accessing table '{ANOMALY_TABLE_NAME}': {e}")
            return 0

    saved = 0
    now_str = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    for i, anomaly in enumerate(anomalies):
        if anomaly["confidence"] >= CONFIDENCE_THRESHOLD:
            try:
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
            except ClientError as e:
                logger.error(f"Failed to write anomaly to '{ANOMALY_TABLE_NAME}': {e}")

    logger.info(f"Saved {saved} high-confidence anomalies (threshold >= {CONFIDENCE_THRESHOLD}) to '{ANOMALY_TABLE_NAME}'")
    return saved


# ─────────────────────────────────────────
# MAIN DETECTION RUN
# ─────────────────────────────────────────
def run_detection():
    start_time = datetime.now(timezone.utc).isoformat()
    logger.info(f"Anomaly detection run started at {start_time}")
    logger.info(f"Confidence threshold: {CONFIDENCE_THRESHOLD}")

    utilization_items = fetch_metrics("utilization")
    billing_items = fetch_metrics("billing")

    ts_anomalies = detect_time_series_anomalies(utilization_items)
    multi_anomalies = detect_multivariate_anomalies(utilization_items, billing_items)

    all_anomalies = ts_anomalies + multi_anomalies

    logger.info(
        f"Detection Summary — Time-series: {len(ts_anomalies)}, "
        f"Multivariate: {len(multi_anomalies)}, Total: {len(all_anomalies)}"
    )

    if all_anomalies:
        for a in all_anomalies:
            if a["confidence"] >= CONFIDENCE_THRESHOLD:
                logger.info(
                    f"🚨 [{a['model']}] {a['anomaly_type']} at {a['timestamp']} — confidence: {a['confidence']}"
                )

    save_anomalies(all_anomalies)
    logger.info("Anomaly detection run complete.")


if __name__ == "__main__":
    run_detection()
