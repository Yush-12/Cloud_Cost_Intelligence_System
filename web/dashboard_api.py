import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone
from flask import Flask, jsonify, send_file
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Ensure repository root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db_utils import scan_all
from src.aws_clients import get_table

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("dashboard_api")

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
    return response


# ─────────────────────────────────────────
# DASHBOARD UI
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
        return jsonify({"status": "error", "message": str(e)}), 500


# ─────────────────────────────────────────
# PANEL 1: Cost Trend
# ─────────────────────────────────────────
@app.route("/api/cost-trend")
def cost_trend():
    try:
        cost_table = get_table(COST_TABLE_NAME)
        items = scan_all(cost_table, Attr("metric_type").eq("billing"))
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return jsonify({
                "error": f"Table '{COST_TABLE_NAME}' not found. Please run 'python scripts/setup_aws.py' first."
            }), 503
        return jsonify({"error": str(e)}), 500

    grouped = {}
    for item in items:
        service = item.get("service", "Unknown")
        if service not in grouped:
            grouped[service] = []
        grouped[service].append({
            "timestamp": item.get("timestamp", ""),
            "cost_usd": float(item.get("cost_usd", 0))
        })

    for service in grouped:
        grouped[service].sort(key=lambda x: x["timestamp"])

    return jsonify(grouped)


# ─────────────────────────────────────────
# PANEL 2: Anomaly Feed
# ─────────────────────────────────────────
@app.route("/api/anomalies")
def anomalies():
    try:
        anomaly_table = get_table(ANOMALY_TABLE_NAME)
        items = scan_all(anomaly_table)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return jsonify({
                "error": f"Table '{ANOMALY_TABLE_NAME}' not found. Please run 'python scripts/setup_aws.py' first."
            }), 503
        return jsonify({"error": str(e)}), 500

    items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    result = []
    for item in items:
        result.append({
            "anomaly_id":   item.get("anomaly_id", ""),
            "timestamp":    item.get("timestamp", ""),
            "anomaly_type": item.get("anomaly_type", ""),
            "model":        item.get("model", ""),
            "confidence":   float(item.get("confidence", 0)),
            "status":       item.get("status", "pending")
        })

    return jsonify(result)


# ─────────────────────────────────────────
# PANEL 3: Optimization Log
# ─────────────────────────────────────────
@app.route("/api/optimization-log")
def optimization_log():
    try:
        audit_table = get_table(AUDIT_TABLE_NAME)
        items = scan_all(audit_table)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return jsonify({
                "error": f"Table '{AUDIT_TABLE_NAME}' not found. Please run 'python scripts/setup_aws.py' first."
            }), 503
        return jsonify({"error": str(e)}), 500

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
            "estimated_saving_usd": float(item.get("estimated_saving_usd", 0)),
            "rollback_command":     item.get("rollback_command", "")
        })

    return jsonify(result)


# ─────────────────────────────────────────
# PANEL 4: Savings Summary
# ─────────────────────────────────────────
@app.route("/api/savings-summary")
def savings_summary():
    try:
        audit_table = get_table(AUDIT_TABLE_NAME)
        anomaly_table = get_table(ANOMALY_TABLE_NAME)
        items = scan_all(audit_table)
        anomaly_items = scan_all(anomaly_table)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return jsonify({
                "error": "DynamoDB tables not found. Please run 'python scripts/setup_aws.py' first."
            }), 503
        return jsonify({"error": str(e)}), 500

    total_saving   = sum(float(i.get("estimated_saving_usd", 0)) for i in items)
    actioned_count = sum(1 for i in items if i.get("status") == "actioned")
    skipped_count  = sum(1 for i in items if i.get("status") == "skipped")
    failed_count   = sum(1 for i in items if i.get("status") == "failed")

    # Savings by resource type
    by_type = {}
    for item in items:
        rtype  = item.get("resource_type", "Unknown")
        saving = float(item.get("estimated_saving_usd", 0))
        by_type[rtype] = round(by_type.get(rtype, 0) + saving, 6)

    # Anomaly breakdown
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


# ─────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────
@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() in ("true", "1", "yes")

    logger.info(f"Dashboard API starting on http://{host}:{port}")
    logger.info(f"Serving dashboard UI at http://localhost:{port}/")
    app.run(host=host, port=port, debug=debug)
