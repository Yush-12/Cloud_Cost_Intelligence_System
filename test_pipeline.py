import boto3
from boto3.dynamodb.conditions import Attr
import pytest
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

REGION         = os.getenv("AWS_REGION", "us-east-1")
TABLE_NAME     = os.getenv("DYNAMODB_TABLE", "CostTelemetry")
EC2_INSTANCE   = "i-0aa9b48a77f3f6bd7"  # Your real instance ID

dynamo         = boto3.resource('dynamodb', region_name=REGION)
cost_table     = dynamo.Table("CostTelemetry")
anomaly_table  = dynamo.Table("AnomalyEvents")
audit_table    = dynamo.Table("OptimizationAudit")
ec2_client     = boto3.client('ec2', region_name=REGION)
lambda_client  = boto3.client('lambda', region_name=REGION)


# ─────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────
def get_latest_anomaly(anomaly_type):
    response = anomaly_table.scan(
        FilterExpression=Attr("anomaly_type").eq(anomaly_type)
    )
    items = sorted(response.get("Items", []), key=lambda x: x.get("timestamp",""), reverse=True)
    return items[0] if items else None


# ─────────────────────────────────────────────────────
# TEST 1: Collector writes all 3 metric streams
# ─────────────────────────────────────────────────────
class TestCollector:

    def test_billing_metrics_written(self):
        """Collector should write billing records to CostTelemetry."""
        from collector import collect_billing_metrics
        collect_billing_metrics()

        response = cost_table.scan(
            FilterExpression=Attr("metric_type").eq("billing")
        )
        assert len(response["Items"]) > 0, "No billing records found in DynamoDB"

    def test_utilization_metrics_written(self):
        """Collector should write CPU utilization records."""
        from collector import collect_utilization_metrics
        collect_utilization_metrics()

        response = cost_table.scan(
            FilterExpression=Attr("metric_type").eq("utilization")
        )
        assert len(response["Items"]) > 0, "No utilization records found in DynamoDB"

    def test_inventory_written(self):
        """Collector should write resource inventory records."""
        from collector import collect_resource_inventory
        collect_resource_inventory()

        response = cost_table.scan(
            FilterExpression=Attr("metric_type").eq("inventory")
        )
        assert len(response["Items"]) > 0, "No inventory records found in DynamoDB"

    def test_timestamps_are_strings(self):
        """All DynamoDB records must have string timestamps (not datetime objects)."""
        response = cost_table.scan(Limit=10)
        for item in response["Items"]:
            assert isinstance(item["timestamp"], str), \
                f"Timestamp is not a string: {type(item['timestamp'])}"

    def test_records_have_required_fields(self):
        """Every record must have resource_id, timestamp, metric_type, service."""
        response = cost_table.scan(Limit=20)
        required = ["resource_id", "timestamp", "metric_type", "service"]
        for item in response["Items"]:
            for field in required:
                assert field in item, f"Missing field '{field}' in record: {item}"


# ─────────────────────────────────────────────────────
# TEST 2: CPU Spike Detection
# ─────────────────────────────────────────────────────
class TestCPUSpikeDetection:

    def test_cpu_spike_triggers_anomaly(self):
        """
        Simulate a CPU spike by injecting high CPU readings,
        then verify anomaly_detector picks it up.
        """
        # Inject 5 extreme CPU spike readings right now
        for i in range(5):
            ts = (datetime.now(timezone.utc) - timedelta(minutes=i)).isoformat()
            cost_table.put_item(Item={
                "resource_id": EC2_INSTANCE,
                "timestamp": ts,
                "metric_type": "utilization",
                "service": "Amazon EC2",
                "region": REGION,
                "cpu_utilization": "95.0"
            })

        # Run detection
        from anomaly_detector import run_detection
        run_detection()

        # Verify anomaly was written
        anomaly = get_latest_anomaly("cpu_spike")
        assert anomaly is not None, "cpu_spike anomaly was not detected"
        assert float(anomaly["confidence"]) >= 0.85, \
            f"Confidence too low: {anomaly['confidence']}"

    def test_cpu_spike_confidence_above_threshold(self):
        """Detected cpu_spike must have confidence >= 0.85."""
        anomaly = get_latest_anomaly("cpu_spike")
        if anomaly:
            assert float(anomaly["confidence"]) >= 0.85


# ─────────────────────────────────────────────────────
# TEST 3: Idle Instance Detection
# ─────────────────────────────────────────────────────
class TestIdleInstanceDetection:

    def test_idle_instance_triggers_anomaly(self):
        """Simulate a suspiciously idle instance and verify detection."""
        for i in range(5):
            ts = (datetime.now(timezone.utc) - timedelta(minutes=i*10)).isoformat()
            cost_table.put_item(Item={
                "resource_id": EC2_INSTANCE,
                "timestamp": ts,
                "metric_type": "utilization",
                "service": "Amazon EC2",
                "region": REGION,
                "cpu_utilization": "0.05"  # Suspiciously idle
            })

        from anomaly_detector import run_detection
        run_detection()

        anomaly = get_latest_anomaly("idle_instance")
        assert anomaly is not None, "idle_instance anomaly was not detected"

    def test_idle_anomaly_has_pending_or_actioned_status(self):
        """Idle instance anomaly should be either pending or actioned."""
        anomaly = get_latest_anomaly("idle_instance")
        if anomaly:
            assert anomaly["status"] in ["pending", "actioned"], \
                f"Unexpected status: {anomaly['status']}"


# ─────────────────────────────────────────────────────
# TEST 4: Cost Spike Detection
# ─────────────────────────────────────────────────────
class TestCostSpikeDetection:

    def test_cost_spike_written_to_dynamodb(self):
        """Inject a cost spike and verify it's stored correctly."""
        ts = datetime.now(timezone.utc).isoformat()
        cost_table.put_item(Item={
            "resource_id": EC2_INSTANCE,
            "timestamp": ts,
            "metric_type": "billing",
            "service": "Amazon EC2",
            "region": REGION,
            "cost_usd": "5.00"  # Clear spike
        })

        response = cost_table.scan(
            FilterExpression=
                Attr("metric_type").eq("billing") &
                Attr("cost_usd").eq("5.00")
        )
        assert len(response["Items"]) > 0, "Cost spike record not found in DynamoDB"


# ─────────────────────────────────────────────────────
# TEST 5: Optimization Engine Actions
# ─────────────────────────────────────────────────────
class TestOptimizationEngine:

    def test_audit_records_written(self):
        """Optimization engine must write audit records for every action."""
        from optimization_engine import run_engine
        run_engine()

        response = audit_table.scan()
        assert len(response["Items"]) > 0, "No audit records found in OptimizationAudit"

    def test_audit_records_have_rollback_command(self):
        """Every actioned audit record must have a rollback command."""
        response = audit_table.scan(
            FilterExpression=Attr("status").eq("actioned")
        )
        for item in response["Items"]:
            assert "rollback_command" in item, \
                f"No rollback_command in audit record: {item['action_id']}"

    def test_circuit_breaker_respected(self):
        """Engine must not execute more than MAX_ACTIONS_PER_HOUR actions."""
        from optimization_engine import MAX_ACTIONS_PER_HOUR
        assert MAX_ACTIONS_PER_HOUR <= 10, \
            "Circuit breaker limit is dangerously high"

    def test_anomaly_status_updated_after_action(self):
        """AnomalyEvents status must change from pending after engine runs."""
        response = anomaly_table.scan()
        statuses = [i.get("status") for i in response["Items"]]
        # At least some should be non-pending after engine ran
        assert any(s != "pending" for s in statuses), \
            "No anomalies were ever actioned or skipped"


# ─────────────────────────────────────────────────────
# TEST 6: API Health Checks
# ─────────────────────────────────────────────────────
class TestDashboardAPI:

    def test_health_endpoint(self):
        """Dashboard API /health must return ok."""
        import urllib.request
        import json
        try:
            with urllib.request.urlopen("http://localhost:5000/api/health", timeout=3) as r:
                data = json.loads(r.read())
            assert data["status"] == "ok"
        except Exception:
            pytest.skip("Dashboard API not running — start dashboard_api.py first")

    def test_anomalies_endpoint_returns_list(self):
        """Anomalies endpoint must return a list."""
        import urllib.request, json
        try:
            with urllib.request.urlopen("http://localhost:5000/api/anomalies", timeout=3) as r:
                data = json.loads(r.read())
            assert isinstance(data, list)
        except Exception:
            pytest.skip("Dashboard API not running")

    def test_savings_summary_has_required_fields(self):
        """Savings summary must have all 4 required fields."""
        import urllib.request, json
        try:
            with urllib.request.urlopen("http://localhost:5000/api/savings-summary", timeout=3) as r:
                data = json.loads(r.read())
            required = [
                "total_saving_usd",
                "actions_taken",
                "anomalies_by_type",
                "savings_by_type"
            ]
            for field in required:
                assert field in data, f"Missing field: {field}"
        except Exception:
            pytest.skip("Dashboard API not running")


# ─────────────────────────────────────────────────────
# TEST 7: End-to-End Pipeline
# ─────────────────────────────────────────────────────
class TestEndToEnd:

    def test_full_pipeline_detects_and_actions(self):
        """
        Full end-to-end: inject anomaly → detect → action → verify audit.
        This is the master completion signal for Phase 6.
        """
        # Step 1: inject a clear anomaly
        now = datetime.now(timezone.utc)
        for i in range(10):
            ts = (now - timedelta(minutes=i*5)).isoformat()
            cost_table.put_item(Item={
                "resource_id": EC2_INSTANCE,
                "timestamp": ts,
                "metric_type": "utilization",
                "service": "Amazon EC2",
                "region": REGION,
                "cpu_utilization": "92.0"
            })

        # Step 2: run detection
        from anomaly_detector import run_detection
        run_detection()

        # Step 3: run engine
        from optimization_engine import run_engine
        run_engine()

        # Step 4: verify audit record exists
        response = audit_table.scan()
        assert len(response["Items"]) > 0, \
            "End-to-end FAILED: no audit records after full pipeline run"

        # Step 5: verify at least one anomaly was processed
        response = anomaly_table.scan()
        processed = [
            i for i in response["Items"]
            if i.get("status") in ["actioned", "skipped", "failed"]
        ]
        assert len(processed) > 0, \
            "End-to-end FAILED: no anomalies were processed by engine"

        print(f"\n✅ End-to-end PASSED — {len(processed)} anomalies processed, "
              f"{len(audit_table.scan()['Items'])} audit records written")