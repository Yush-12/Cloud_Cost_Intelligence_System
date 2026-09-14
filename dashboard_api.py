from flask import Flask, jsonify
import boto3
from boto3.dynamodb.conditions import Attr
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from db_utils import scan_all

load_dotenv()

app = Flask(__name__)

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

REGION = os.getenv("AWS_REGION", "us-east-1")
dynamo = boto3.resource('dynamodb', region_name=REGION)

cost_table     = dynamo.Table("CostTelemetry")
anomaly_table  = dynamo.Table("AnomalyEvents")
audit_table    = dynamo.Table("OptimizationAudit")


# ─────────────────────────────────────────
# PANEL 1: Cost Trend
# ─────────────────────────────────────────
@app.route("/api/cost-trend")
def cost_trend():
    items = scan_all(
        cost_table,
        Attr("metric_type").eq("billing")
    )

    # Group by service and sort by timestamp
    grouped = {}
    for item in items:
        service = item.get("service", "Unknown")
        if service not in grouped:
            grouped[service] = []
        grouped[service].append({
            "timestamp": item.get("timestamp", ""),
            "cost_usd": float(item.get("cost_usd", 0))
        })

    # Sort each service by time
    for service in grouped:
        grouped[service].sort(key=lambda x: x["timestamp"])

    return jsonify(grouped)


# ─────────────────────────────────────────
# PANEL 2: Anomaly Feed
# ─────────────────────────────────────────
@app.route("/api/anomalies")
def anomalies():
    items = scan_all(anomaly_table)
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
    items = scan_all(audit_table)
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
    items = scan_all(audit_table)

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
    anomaly_items = scan_all(anomaly_table)
    by_anomaly = {}
    for item in anomaly_items:
        atype = item.get("anomaly_type", "Unknown")
        by_anomaly[atype] = by_anomaly.get(atype, 0) + 1

    return jsonify({
        "total_saving_usd":  round(total_saving, 4),
        "total_saving_daily": round(total_saving * 24, 4),
        "total_saving_monthly": round(total_saving * 24 * 30, 4),
        "actions_taken":     actioned_count,
        "actions_skipped":   skipped_count,
        "actions_failed":    failed_count,
        "savings_by_type":   by_type,
        "anomalies_by_type": by_anomaly
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
    print("\n🚀 Dashboard API starting...")
    print("   API running at: http://localhost:5000")
    print("   Open dashboard.html in your browser\n")
    app.run(debug=True, port=5000)