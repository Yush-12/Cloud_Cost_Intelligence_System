import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, send_file, request
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Ensure repository root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db_utils import scan_all, safe_float, safe_int
from src.aws_clients import get_table, is_simulation_mode
from src.synthetic_data import seed_historical_data

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("dashboard_api")

# Pre-seed in-memory store if in simulation mode
if is_simulation_mode():
    try:
        seed_historical_data()
        logger.info("Simulation mode active: In-memory store pre-seeded with baseline telemetry.")
    except Exception as e:
        logger.warning(f"Could not pre-seed simulation store: {e}")


app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_FILE = os.path.join(BASE_DIR, "dashboard.html")

COST_TABLE_NAME = os.getenv("DYNAMODB_TABLE", "CostTelemetry")
ANOMALY_TABLE_NAME = os.getenv("ANOMALY_TABLE", "AnomalyEvents")
AUDIT_TABLE_NAME = os.getenv("AUDIT_TABLE", "OptimizationAudit")


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


# ─────────────────────────────────────────
# DEMO DATA GENERATORS (Portfolio & Preview)
# ─────────────────────────────────────────
def generate_demo_cost_trend():
    """Generates 7 days of realistic time-series billing data across core AWS services."""
    now = datetime.now(timezone.utc)
    services = {
        "Amazon EC2": {"base": 0.42, "variance": 0.08, "spike_day": 3, "spike_val": 1.28},
        "AWS Lambda": {"base": 0.08, "variance": 0.03, "spike_day": 5, "spike_val": 0.54},
        "Amazon RDS": {"base": 0.65, "variance": 0.04, "spike_day": None, "spike_val": 0},
        "Amazon S3": {"base": 0.18, "variance": 0.02, "spike_day": None, "spike_val": 0},
        "Amazon CloudWatch": {"base": 0.06, "variance": 0.01, "spike_day": None, "spike_val": 0}
    }
    grouped = {}
    for svc, config in services.items():
        grouped[svc] = []
        for i in range(28, -1, -1):
            point_time = now - timedelta(hours=i * 6)
            day_index = (28 - i) // 4
            cost = config["base"] + ((i % 5) * 0.015)
            if config["spike_day"] is not None and day_index == config["spike_day"]:
                cost += config["spike_val"]
            grouped[svc].append({
                "timestamp": point_time.isoformat(),
                "cost_usd": round(cost, 4)
            })
    return grouped


def generate_demo_anomalies():
    """Generates high-signal realistic anomaly records."""
    now = datetime.now(timezone.utc)
    return [
        {
            "anomaly_id": "anom-ec2-cpu-9842",
            "timestamp": (now - timedelta(minutes=42)).isoformat(),
            "anomaly_type": "cpu_spike",
            "model": "isolation_forest",
            "confidence": 0.94,
            "status": "actioned",
            "resource_id": "i-09f482d87e1a3b90 (t3.xlarge)",
            "details": "Sustained CPU at 98.4% for >120m without autoscaling trigger"
        },
        {
            "anomaly_id": "anom-lambda-runaway-01",
            "timestamp": (now - timedelta(hours=2, minutes=15)).isoformat(),
            "anomaly_type": "runaway_function",
            "model": "z_score_detector",
            "confidence": 0.97,
            "status": "actioned",
            "resource_id": "image-resizer-prod",
            "details": "Recursive S3 event trigger: 48,000 invocations/min"
        },
        {
            "anomaly_id": "anom-ec2-idle-8821",
            "timestamp": (now - timedelta(hours=5, minutes=10)).isoformat(),
            "anomaly_type": "idle_instance",
            "model": "moving_average_zscore",
            "confidence": 0.89,
            "status": "pending",
            "resource_id": "i-04a7bc901e82f143 (c5.2xlarge)",
            "details": "Average CPU < 0.8% and network I/O < 1MB for 72 consecutive hours"
        },
        {
            "anomaly_id": "anom-rds-iops-5519",
            "timestamp": (now - timedelta(hours=8, minutes=30)).isoformat(),
            "anomaly_type": "cost_spike",
            "model": "isolation_forest",
            "confidence": 0.91,
            "status": "pending",
            "resource_id": "analytics-aurora-cluster",
            "details": "Provisioned IOPS billing escalated by +$18.50/hr due to unindexed join"
        },
        {
            "anomaly_id": "anom-ebs-unattached-1044",
            "timestamp": (now - timedelta(hours=14, minutes=5)).isoformat(),
            "anomaly_type": "unattached_ebs",
            "model": "rule_based_filter",
            "confidence": 0.99,
            "status": "actioned",
            "resource_id": "vol-0a817b26d3e4901f (500 GiB gp3)",
            "details": "Volume unattached and orphaned for 18 days"
        },
        {
            "anomaly_id": "anom-lambda-overprov-772",
            "timestamp": (now - timedelta(days=1, hours=2)).isoformat(),
            "anomaly_type": "overprovisioned_memory",
            "model": "z_score_detector",
            "confidence": 0.86,
            "status": "skipped",
            "resource_id": "auth-token-verifier",
            "details": "Allocated 3,008 MB, peak memory utilized was 84 MB (97% headroom)"
        }
    ]


def generate_demo_optimization_log():
    """Generates execution history with autonomous actions and safe rollback commands."""
    now = datetime.now(timezone.utc)
    return [
        {
            "action_id": "act-opt-01",
            "timestamp": (now - timedelta(minutes=40)).isoformat(),
            "anomaly_type": "cpu_spike",
            "action_taken": "scale_out_or_rebalance",
            "resource_id": "i-09f482d87e1a3b90",
            "resource_type": "Amazon EC2",
            "status": "actioned",
            "estimated_saving_usd": 0.2850,
            "rollback_command": "aws autoscaling set-desired-capacity --auto-scaling-group-name api-asg --desired-capacity 2"
        },
        {
            "action_id": "act-opt-02",
            "timestamp": (now - timedelta(hours=2, minutes=10)).isoformat(),
            "anomaly_type": "runaway_function",
            "action_taken": "throttle_concurrency",
            "resource_id": "image-resizer-prod",
            "resource_type": "AWS Lambda",
            "status": "actioned",
            "estimated_saving_usd": 0.5200,
            "rollback_command": "aws lambda put-function-concurrency --function-name image-resizer-prod --reserved-concurrent-executions 50"
        },
        {
            "action_id": "act-opt-03",
            "timestamp": (now - timedelta(hours=13, minutes=50)).isoformat(),
            "anomaly_type": "unattached_ebs",
            "action_taken": "snapshot_and_delete_volume",
            "resource_id": "vol-0a817b26d3e4901f",
            "resource_type": "Amazon EBS",
            "status": "actioned",
            "estimated_saving_usd": 0.0550,
            "rollback_command": "aws ec2 create-volume --snapshot-id snap-0928a8f11 --availability-zone ap-south-1a"
        },
        {
            "action_id": "act-opt-04",
            "timestamp": (now - timedelta(hours=22, minutes=15)).isoformat(),
            "anomaly_type": "gp2_legacy_volume",
            "action_taken": "upgrade_gp2_to_gp3",
            "resource_id": "vol-07823fbc01824aa3",
            "resource_type": "Amazon EBS",
            "status": "actioned",
            "estimated_saving_usd": 0.0820,
            "rollback_command": "aws ec2 modify-volume --volume-id vol-07823fbc01824aa3 --volume-type gp2"
        },
        {
            "action_id": "act-opt-05",
            "timestamp": (now - timedelta(days=1, hours=6)).isoformat(),
            "anomaly_type": "s3_standard_infrequent",
            "action_taken": "apply_lifecycle_intelligent_tiering",
            "resource_id": "ml-checkpoints-prod-2025",
            "resource_type": "Amazon S3",
            "status": "actioned",
            "estimated_saving_usd": 0.1450,
            "rollback_command": "aws s3api delete-bucket-intelligent-tiering-configuration --bucket ml-checkpoints-prod-2025 --id TieringRule"
        },
        {
            "action_id": "act-opt-06",
            "timestamp": (now - timedelta(days=1, hours=18)).isoformat(),
            "anomaly_type": "idle_instance",
            "action_taken": "stop_idle_instance",
            "resource_id": "i-038ba716c90e2114",
            "resource_type": "Amazon EC2",
            "status": "skipped",
            "estimated_saving_usd": 0.0,
            "rollback_command": "none"
        }
    ]


def generate_demo_savings_summary():
    """Aggregated stats and cost savings breakdown."""
    return {
        "total_saving_usd": 1.0870,
        "total_saving_daily": 26.0880,
        "total_saving_monthly": 782.6400,
        "actions_taken": 5,
        "actions_skipped": 1,
        "actions_failed": 0,
        "savings_by_type": {
            "Amazon EC2": 0.2850,
            "AWS Lambda": 0.5200,
            "Amazon EBS": 0.1370,
            "Amazon S3": 0.1450
        },
        "anomalies_by_type": {
            "cpu_spike": 4,
            "runaway_function": 2,
            "idle_instance": 3,
            "cost_spike": 2,
            "unattached_ebs": 2,
            "overprovisioned_memory": 1
        }
    }


# ─────────────────────────────────────────
# DASHBOARD UI SERVING
# ─────────────────────────────────────────
@app.route("/")
@app.route("/dashboard")
@app.route("/dashboard.html")
def serve_dashboard():
    """Serves the single-page dashboard HTML directly from Flask."""
    candidates = [
        DASHBOARD_FILE,
        os.path.join(os.getcwd(), "web", "dashboard.html"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "dashboard.html"),
        os.path.join(os.path.abspath(os.sep), "var", "task", "web", "dashboard.html"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return send_file(path)
    return (
        "<h3>dashboard.html not found. Ensure it exists in web/dashboard.html.</h3>",
        404
    )


# ─────────────────────────────────────────
# PIPELINE TRIGGER / CRON
# ─────────────────────────────────────────
@app.route("/api/run-pipeline", methods=["GET", "POST"])
@app.route("/api/cron", methods=["GET", "POST"])
def trigger_pipeline():
    """Trigger a single cycle of the cost intelligence pipeline (for Vercel cron or UI button)."""
    try:
        from src.pipeline import run_pipeline_once
        run_pipeline_once()
        return jsonify({
            "status": "success",
            "message": "Pipeline cycle executed successfully",
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        logger.error(f"Error executing pipeline trigger: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ─────────────────────────────────────────
# PANEL 1: Cost Trend
# ─────────────────────────────────────────
@app.route("/api/cost-trend")
def cost_trend():
    mode = request.args.get("mode", "auto").lower()
    
    if mode != "demo":
        try:
            cost_table = get_table(COST_TABLE_NAME)
            items = scan_all(cost_table, Attr("metric_type").eq("billing"))
            if items:
                grouped = {}
                for item in items:
                    service = item.get("service", "Unknown")
                    if service not in grouped:
                        grouped[service] = []
                    grouped[service].append({
                        "timestamp": item.get("timestamp", ""),
                        "cost_usd": safe_float(item.get("cost_usd"), 0.0)
                    })
                for service in grouped:
                    grouped[service].sort(key=lambda x: x["timestamp"])
                return jsonify(grouped)
            elif mode == "live":
                return jsonify({})
        except Exception as e:
            logger.warning(f"Live cost-trend query exception: {e}")
            if mode == "live":
                return jsonify({"error": str(e)}), 200

    # Auto fallback or demo requested
    return jsonify(generate_demo_cost_trend())


# ─────────────────────────────────────────
# PANEL 2: Anomaly Feed
# ─────────────────────────────────────────
@app.route("/api/anomalies")
def anomalies():
    mode = request.args.get("mode", "auto").lower()

    if mode != "demo":
        try:
            anomaly_table = get_table(ANOMALY_TABLE_NAME)
            items = scan_all(anomaly_table)
            if items:
                items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
                result = []
                for item in items:
                    result.append({
                        "anomaly_id":   item.get("anomaly_id", ""),
                        "timestamp":    item.get("timestamp", ""),
                        "anomaly_type": item.get("anomaly_type", ""),
                        "model":        item.get("model", ""),
                        "confidence":   safe_float(item.get("confidence"), 0.0),
                        "status":       item.get("status", "pending"),
                        "resource_id":  item.get("resource_id", ""),
                        "details":      item.get("details", "")
                    })
                return jsonify(result)
            elif mode == "live":
                return jsonify([])
        except Exception as e:
            logger.warning(f"Live anomalies query exception: {e}")
            if mode == "live":
                return jsonify([]), 200

    return jsonify(generate_demo_anomalies())


# ─────────────────────────────────────────
# PANEL 3: Optimization Log
# ─────────────────────────────────────────
@app.route("/api/optimization-log")
def optimization_log():
    mode = request.args.get("mode", "auto").lower()

    if mode != "demo":
        try:
            audit_table = get_table(AUDIT_TABLE_NAME)
            items = scan_all(audit_table)
            if items:
                items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
                result = []
                for item in items:
                    result.append({
                        "action_id":            item.get("action_id", ""),
                        "timestamp":            item.get("timestamp", ""),
                        "anomaly_type":         item.get("anomaly_type", ""),
                        "action_taken":         item.get("action_taken", ""),
                        "resource_id":          item.get("resource_id", ""),
                        "resource_type":        item.get("resource_type", ""),
                        "status":               item.get("status", ""),
                        "estimated_saving_usd": safe_float(item.get("estimated_saving_usd"), 0.0),
                        "rollback_command":     item.get("rollback_command", "")
                    })
                return jsonify(result)
            elif mode == "live":
                return jsonify([])
        except Exception as e:
            logger.warning(f"Live optimization-log query exception: {e}")
            if mode == "live":
                return jsonify([]), 200

    return jsonify(generate_demo_optimization_log())


# ─────────────────────────────────────────
# PANEL 4: Savings Summary
# ─────────────────────────────────────────
@app.route("/api/savings-summary")
def savings_summary():
    mode = request.args.get("mode", "auto").lower()

    if mode != "demo":
        try:
            audit_table = get_table(AUDIT_TABLE_NAME)
            anomaly_table = get_table(ANOMALY_TABLE_NAME)
            items = scan_all(audit_table)
            anomaly_items = scan_all(anomaly_table)

            if items or anomaly_items:
                total_saving   = sum(safe_float(i.get("estimated_saving_usd"), 0.0) for i in items)
                actioned_count = sum(1 for i in items if i.get("status") == "actioned")
                skipped_count  = sum(1 for i in items if i.get("status") == "skipped")
                failed_count   = sum(1 for i in items if i.get("status") == "failed")

                by_type = {}
                for item in items:
                    rtype  = item.get("resource_type", "Unknown")
                    saving = safe_float(item.get("estimated_saving_usd"), 0.0)
                    by_type[rtype] = round(by_type.get(rtype, 0) + saving, 6)

                by_anomaly = {}
                for item in anomaly_items:
                    atype = item.get("anomaly_type", "Unknown")
                    by_anomaly[atype] = by_anomaly.get(atype, 0) + 1

                return jsonify({
                    "total_saving_usd":   round(total_saving, 4),
                    "total_saving_daily": round(total_saving * 24, 4),
                    "total_saving_monthly": round(total_saving * 24 * 30, 4),
                    "actions_taken":      actioned_count,
                    "actions_skipped":    skipped_count,
                    "actions_failed":     failed_count,
                    "savings_by_type":    by_type,
                    "anomalies_by_type":  by_anomaly
                })
            elif mode == "live":
                return jsonify({
                    "total_saving_usd": 0.0,
                    "total_saving_daily": 0.0,
                    "total_saving_monthly": 0.0,
                    "actions_taken": 0,
                    "actions_skipped": 0,
                    "actions_failed": 0,
                    "savings_by_type": {},
                    "anomalies_by_type": {}
                })
        except Exception as e:
            logger.warning(f"Live savings-summary query exception: {e}")
            if mode == "live":
                return jsonify({
                    "total_saving_usd": 0.0,
                    "total_saving_daily": 0.0,
                    "total_saving_monthly": 0.0,
                    "actions_taken": 0,
                    "actions_skipped": 0,
                    "actions_failed": 0,
                    "savings_by_type": {},
                    "anomalies_by_type": {}
                }), 200

    return jsonify(generate_demo_savings_summary())


# ─────────────────────────────────────────
# FULL DEMO DATA ENDPOINT
# ─────────────────────────────────────────
@app.route("/api/demo-data")
def demo_data():
    """Returns complete bundled simulation dataset in a single call."""
    return jsonify({
        "cost_trend": generate_demo_cost_trend(),
        "anomalies": generate_demo_anomalies(),
        "optimization_log": generate_demo_optimization_log(),
        "savings_summary": generate_demo_savings_summary()
    })


# ─────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────
@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "Cloud Cost Intelligence API",
        "region": os.getenv("AWS_REGION", "us-east-1"),
        "simulation_mode": is_simulation_mode(),
        "timestamp": datetime.now(timezone.utc).isoformat()
    })



if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() in ("true", "1", "yes")

    logger.info(f"Dashboard API starting on http://{host}:{port}")
    logger.info(f"Serving dashboard UI at http://localhost:{port}/")
    app.run(host=host, port=port, debug=debug)
