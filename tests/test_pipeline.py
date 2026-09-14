import os
import sys
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
import boto3
from boto3.dynamodb.conditions import Attr

try:
    import importlib
    moto_module = importlib.import_module("moto")
    mock_aws = getattr(moto_module, "mock_aws", None)
except (ImportError, AttributeError):
    mock_aws = None

# Ensure repository root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ensure test AWS credentials
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_REGION"] = "us-east-1"

from src.aws_clients import (
    get_table,
    get_ec2_client,
    get_s3_client,
    REGION
)
from scripts.setup_aws import setup_tables
from src.collector import (
    collect_billing_metrics,
    collect_utilization_metrics,
    collect_resource_inventory
)
from src.anomaly_detector import run_detection
from src.optimization_engine import run_engine, MAX_ACTIONS_PER_HOUR
from web.dashboard_api import app as flask_app
from src.rollback import rollback_audit_actions


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    """Set test environment variables for every test."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("DYNAMODB_TABLE", "CostTelemetry")
    monkeypatch.setenv("ANOMALY_TABLE", "AnomalyEvents")
    monkeypatch.setenv("AUDIT_TABLE", "OptimizationAudit")


@pytest.fixture
def mock_setup():
    """Initializes mock DynamoDB tables and sample EC2/Lambda resources."""
    if mock_aws is None:
        pytest.skip("moto is required for offline tests. Run 'pip install -r requirements-dev.txt'")
    with mock_aws():
        # Setup tables
        setup_tables()
        cost_table = get_table("CostTelemetry")
        anomaly_table = get_table("AnomalyEvents")
        audit_table = get_table("OptimizationAudit")

        # Create mock EC2 instance
        ec2 = get_ec2_client()
        reservation = ec2.run_instances(
            ImageId="ami-12345678",
            InstanceType="t2.micro",
            MinCount=1,
            MaxCount=1
        )
        instance_id = reservation["Instances"][0]["InstanceId"]

        # Create mock S3 bucket
        s3 = get_s3_client()
        s3.create_bucket(Bucket="test-cost-intel-bucket")

        yield {
            "instance_id": instance_id,
            "cost_table": cost_table,
            "anomaly_table": anomaly_table,
            "audit_table": audit_table,
        }


def get_latest_anomaly(anomaly_type):
    table = get_table("AnomalyEvents")
    response = table.scan(FilterExpression=Attr("anomaly_type").eq(anomaly_type))
    items = sorted(response.get("Items", []), key=lambda x: x.get("timestamp", ""), reverse=True)
    return items[0] if items else None


# ─────────────────────────────────────────────────────
# TEST 1: Collector writes all 3 metric streams
# ─────────────────────────────────────────────────────
class TestCollector:

    def test_billing_metrics_written(self, mock_setup):
        """Collector should write billing records to CostTelemetry."""
        collect_billing_metrics()
        table = mock_setup["cost_table"]
        response = table.scan(FilterExpression=Attr("metric_type").eq("billing"))
        assert len(response["Items"]) > 0, "No billing records found in DynamoDB"

    def test_utilization_metrics_written(self, mock_setup):
        """Collector should write CPU utilization records."""
        collect_utilization_metrics()
        table = mock_setup["cost_table"]
        response = table.scan(FilterExpression=Attr("metric_type").eq("utilization"))
        assert len(response["Items"]) > 0, "No utilization records found in DynamoDB"

    def test_inventory_written(self, mock_setup):
        """Collector should write resource inventory records."""
        collect_resource_inventory()
        table = mock_setup["cost_table"]
        response = table.scan(FilterExpression=Attr("metric_type").eq("inventory"))
        assert len(response["Items"]) > 0, "No inventory records found in DynamoDB"

    def test_timestamps_are_strings(self, mock_setup):
        """All DynamoDB records must have ISO string timestamps."""
        collect_billing_metrics()
        table = mock_setup["cost_table"]
        response = table.scan(Limit=10)
        for item in response["Items"]:
            assert isinstance(item["timestamp"], str), f"Timestamp is not a string: {type(item['timestamp'])}"

    def test_records_have_required_fields(self, mock_setup):
        """Every record must have resource_id, timestamp, metric_type, service."""
        collect_billing_metrics()
        table = mock_setup["cost_table"]
        response = table.scan(Limit=20)
        required = ["resource_id", "timestamp", "metric_type", "service"]
        for item in response["Items"]:
            for field in required:
                assert field in item, f"Missing field '{field}' in record: {item}"


# ─────────────────────────────────────────────────────
# TEST 2: CPU Spike Detection
# ─────────────────────────────────────────────────────
class TestCPUSpikeDetection:

    def test_cpu_spike_triggers_anomaly(self, mock_setup):
        """Simulate high CPU utilization and verify anomaly_detector detects it."""
        cost_table = mock_setup["cost_table"]
        instance_id = mock_setup["instance_id"]

        now = datetime.now(timezone.utc)
        # Inject normal baseline followed by an extreme spike
        for i in range(10):
            ts = (now - timedelta(minutes=(15 - i) * 5)).isoformat()
            cpu = "5.0" if i < 8 else "95.0"
            cost_table.put_item(Item={
                "resource_id": instance_id,
                "timestamp": ts,
                "metric_type": "utilization",
                "service": "Amazon EC2",
                "region": REGION,
                "cpu_utilization": cpu
            })

        run_detection()

        anomaly = get_latest_anomaly("cpu_spike")
        assert anomaly is not None, "cpu_spike anomaly was not detected"
        assert float(anomaly["confidence"]) >= 0.85, f"Confidence too low: {anomaly['confidence']}"


# ─────────────────────────────────────────────────────
# TEST 3: Idle Instance Detection
# ─────────────────────────────────────────────────────
class TestIdleInstanceDetection:

    def test_idle_instance_triggers_anomaly(self, mock_setup):
        """Simulate a suspiciously idle instance and verify detection."""
        cost_table = mock_setup["cost_table"]
        instance_id = mock_setup["instance_id"]

        now = datetime.now(timezone.utc)
        for i in range(10):
            ts = (now - timedelta(minutes=(15 - i) * 5)).isoformat()
            cpu = "20.0" if i < 7 else "0.05"
            cost_table.put_item(Item={
                "resource_id": instance_id,
                "timestamp": ts,
                "metric_type": "utilization",
                "service": "Amazon EC2",
                "region": REGION,
                "cpu_utilization": cpu
            })

        run_detection()

        anomaly = get_latest_anomaly("idle_instance")
        assert anomaly is not None, "idle_instance anomaly was not detected"
        assert float(anomaly["confidence"]) >= 0.85


# ─────────────────────────────────────────────────────
# TEST 4: Cost Spike Detection
# ─────────────────────────────────────────────────────
class TestCostSpikeDetection:

    def test_cost_spike_written_to_dynamodb(self, mock_setup):
        """Inject a cost spike and verify multivariate detection catches it."""
        cost_table = mock_setup["cost_table"]
        instance_id = mock_setup["instance_id"]

        ts = datetime.now(timezone.utc).isoformat()
        cost_table.put_item(Item={
            "resource_id": instance_id,
            "timestamp": ts,
            "metric_type": "billing",
            "service": "Amazon EC2",
            "region": REGION,
            "cost_usd": "5.00"
        })

        run_detection()

        anomaly = get_latest_anomaly("cost_spike")
        assert anomaly is not None, "cost_spike anomaly was not detected"


# ─────────────────────────────────────────────────────
# TEST 5: Optimization Engine Actions
# ─────────────────────────────────────────────────────
class TestOptimizationEngine:

    def test_audit_records_written(self, mock_setup):
        """Optimization engine must write audit records for every action."""
        anomaly_table = mock_setup["anomaly_table"]
        audit_table = mock_setup["audit_table"]

        # Insert a pending idle_instance anomaly
        ts = datetime.now(timezone.utc).isoformat()
        anomaly_table.put_item(Item={
            "anomaly_id": "test-anomaly-idle",
            "timestamp": ts,
            "model": "statistical_time_series",
            "anomaly_type": "idle_instance",
            "confidence": "0.95",
            "status": "pending",
            "action_taken": "none"
        })

        run_engine()

        items = audit_table.scan().get("Items", [])
        assert len(items) > 0, "No audit records found in OptimizationAudit"

    def test_audit_records_have_rollback_command(self, mock_setup):
        """Every actioned audit record must have a rollback command."""
        anomaly_table = mock_setup["anomaly_table"]
        audit_table = mock_setup["audit_table"]

        ts = datetime.now(timezone.utc).isoformat()
        anomaly_table.put_item(Item={
            "anomaly_id": "test-anomaly-stop",
            "timestamp": ts,
            "model": "statistical_time_series",
            "anomaly_type": "idle_instance",
            "confidence": "0.95",
            "status": "pending",
            "action_taken": "none"
        })

        run_engine()

        items = audit_table.scan(FilterExpression=Attr("status").eq("actioned")).get("Items", [])
        for item in items:
            assert "rollback_command" in item, f"No rollback_command in audit record: {item}"

    def test_circuit_breaker_limit(self):
        """Engine circuit breaker limit must be <= 10 for safety."""
        assert MAX_ACTIONS_PER_HOUR <= 10, "Circuit breaker limit is dangerously high"

    def test_anomaly_status_updated_after_action(self, mock_setup):
        """AnomalyEvents status must change from pending after engine runs."""
        anomaly_table = mock_setup["anomaly_table"]

        ts = datetime.now(timezone.utc).isoformat()
        anomaly_table.put_item(Item={
            "anomaly_id": "test-anomaly-update",
            "timestamp": ts,
            "model": "statistical_time_series",
            "anomaly_type": "idle_instance",
            "confidence": "0.95",
            "status": "pending",
            "action_taken": "none"
        })

        run_engine()

        item = anomaly_table.get_item(Key={"anomaly_id": "test-anomaly-update", "timestamp": ts}).get("Item")
        assert item is not None
        assert item.get("status") in ["actioned", "skipped"], f"Status not updated: {item.get('status')}"


# ─────────────────────────────────────────────────────
# TEST 6: Dashboard API (using Flask test client)
# ─────────────────────────────────────────────────────
class TestDashboardAPI:

    def test_health_endpoint(self, mock_setup):
        """Health check returns 200 and status ok."""
        client = flask_app.test_client()
        res = client.get("/api/health")
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "ok"

    def test_anomalies_endpoint_returns_list(self, mock_setup):
        """Anomalies endpoint returns 200 and a list."""
        client = flask_app.test_client()
        res = client.get("/api/anomalies")
        assert res.status_code == 200
        assert isinstance(res.get_json(), list)

    def test_savings_summary_has_required_fields(self, mock_setup):
        """Savings summary endpoint returns all required fields."""
        client = flask_app.test_client()
        res = client.get("/api/savings-summary")
        assert res.status_code == 200
        data = res.get_json()
        required = ["total_saving_usd", "actions_taken", "savings_by_type", "anomalies_by_type"]
        for field in required:
            assert field in data, f"Missing required field: {field}"

    def test_serve_dashboard_html(self, mock_setup):
        """Root / serves dashboard.html with 200."""
        client = flask_app.test_client()
        res = client.get("/")
        assert res.status_code == 200
        assert b"AWS Cost Intelligence" in res.data or b"<!DOCTYPE html>" in res.data


# ─────────────────────────────────────────────────────
# TEST 7: End-to-End Pipeline & Rollback
# ─────────────────────────────────────────────────────
class TestEndToEndAndRollback:

    def test_full_pipeline_detects_actions_and_rolls_back(self, mock_setup):
        """
        Full lifecycle:
        1. Inject telemetry
        2. Detect anomalies
        3. Optimize / action
        4. Verify audit record
        5. Roll back action
        """
        cost_table = mock_setup["cost_table"]
        audit_table = mock_setup["audit_table"]
        anomaly_table = mock_setup["anomaly_table"]
        instance_id = mock_setup["instance_id"]

        now = datetime.now(timezone.utc)
        for i in range(10):
            ts = (now - timedelta(minutes=(15 - i) * 5)).isoformat()
            cost_table.put_item(Item={
                "resource_id": instance_id,
                "timestamp": ts,
                "metric_type": "utilization",
                "service": "Amazon EC2",
                "region": REGION,
                "cpu_utilization": "0.1"  # Idle instance
            })

        # Run pipeline
        run_detection()
        run_engine()

        # Verify audit
        audit_items = audit_table.scan().get("Items", [])
        assert len(audit_items) > 0, "No audit records created during end-to-end run"

        actioned_items = [i for i in audit_items if i.get("status") == "actioned"]
        assert len(actioned_items) > 0, "No actions were executed"

        # Test Rollback
        rollback_audit_actions(dry_run=False)

        # Verify audit record status changed to rolled_back
        refreshed_audit = audit_table.scan().get("Items", [])
        rolled_back_items = [i for i in refreshed_audit if i.get("status") == "rolled_back"]
        assert len(rolled_back_items) > 0, "Audit record status was not updated to rolled_back"
