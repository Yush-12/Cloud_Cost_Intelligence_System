"""
tests/test_simulation_mode.py — Comprehensive tests for Zero-Cost Simulation Mode.
Tests:
  - InMemoryTable conditions, CRUD, and expression evaluation
  - Simulated AWS clients (EC2, Lambda, CloudWatch, S3, STS)
  - End-to-end pipeline execution entirely in-memory with zero AWS dependencies
  - Rollback execution in simulation mode
  - Dashboard API endpoints reading from in-memory store
"""

import os
import sys
import pytest
from pathlib import Path
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Attr

# Ensure repository root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_store import (
    InMemoryTable,
    get_in_memory_table,
    reset_in_memory_store,
    get_simulated_ec2_client,
    get_simulated_lambda_client,
    get_simulated_cloudwatch_client,
    get_simulated_s3_client,
    get_simulated_sts_client,
)
from src.synthetic_data import seed_historical_data
from src.aws_clients import get_table
from src.collector import run_collector
from src.anomaly_detector import run_detection
from src.optimization_engine import run_engine
from src.rollback import rollback_audit_actions
from web.dashboard_api import app as flask_app


@pytest.fixture(autouse=True)
def simulation_env(monkeypatch):
    """Ensure simulation mode is enabled for this test suite."""
    monkeypatch.setenv("SIMULATION_MODE", "true")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("DYNAMODB_TABLE", "CostTelemetry")
    monkeypatch.setenv("ANOMALY_TABLE", "AnomalyEvents")
    monkeypatch.setenv("AUDIT_TABLE", "OptimizationAudit")
    reset_in_memory_store()
    yield
    reset_in_memory_store()


# ─────────────────────────────────────────────────────────────────────────────
# Test In-Memory Table Mechanics
# ─────────────────────────────────────────────────────────────────────────────
class TestInMemoryTable:
    def test_put_and_scan(self):
        table = InMemoryTable("TestTable")
        table.put_item(Item={"id": "1", "type": "A", "val": 10})
        table.put_item(Item={"id": "2", "type": "B", "val": 20})
        table.put_item(Item={"id": "3", "type": "A", "val": 30})

        # Unfiltered scan
        res = table.scan()
        assert len(res["Items"]) == 3

        # Attr filter: type == 'A'
        res_a = table.scan(FilterExpression=Attr("type").eq("A"))
        assert len(res_a["Items"]) == 2
        assert all(it["type"] == "A" for it in res_a["Items"])

        # AND condition: type == 'A' & val > 15
        res_and = table.scan(FilterExpression=Attr("type").eq("A") & Attr("val").gt(15))
        assert len(res_and["Items"]) == 1
        assert res_and["Items"][0]["id"] == "3"

    def test_update_item(self):
        table = InMemoryTable("TestTable")
        table.put_item(Item={"anomaly_id": "anom-1", "timestamp": "2026-01-01", "status": "pending"})

        # Update status to actioned
        table.update_item(
            Key={"anomaly_id": "anom-1", "timestamp": "2026-01-01"},
            UpdateExpression="SET #s = :s, action_taken = :a",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "actioned", ":a": "stop_instances"}
        )

        item = table.get_item(Key={"anomaly_id": "anom-1", "timestamp": "2026-01-01"}).get("Item")
        assert item is not None
        assert item["status"] == "actioned"
        assert item["action_taken"] == "stop_instances"

    def test_delete_item(self):
        table = InMemoryTable("TestTable")
        table.put_item(Item={"id": "abc"})
        assert table.item_count == 1
        table.delete_item(Key={"id": "abc"})
        assert table.item_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test Simulated AWS Clients
# ─────────────────────────────────────────────────────────────────────────────
class TestSimulatedClients:
    def test_simulated_ec2_client(self):
        client = get_simulated_ec2_client()
        resp = client.describe_instances()
        instances = resp["Reservations"][0]["Instances"]
        assert len(instances) >= 5

        # Stop instance
        target_id = "i-09f482d87e1a3b90"
        stop_resp = client.stop_instances(InstanceIds=[target_id])
        assert stop_resp["StoppingInstances"][0]["CurrentState"]["Name"] == "stopped"

        # Start instance
        start_resp = client.start_instances(InstanceIds=[target_id])
        assert start_resp["StartingInstances"][0]["CurrentState"]["Name"] == "running"

        # Tag instance
        client.create_tags(Resources=[target_id], Tags=[{"Key": "test-tag", "Value": "active"}])
        inst = [i for i in client.describe_instances()["Reservations"][0]["Instances"] if i["InstanceId"] == target_id][0]
        tag_keys = [t["Key"] for t in inst.get("Tags", [])]
        assert "test-tag" in tag_keys

        # Delete tag
        client.delete_tags(Resources=[target_id], Tags=[{"Key": "test-tag"}])
        inst_after = [i for i in client.describe_instances()["Reservations"][0]["Instances"] if i["InstanceId"] == target_id][0]
        tag_keys_after = [t["Key"] for t in inst_after.get("Tags", [])]
        assert "test-tag" not in tag_keys_after

    def test_simulated_lambda_client(self):
        client = get_simulated_lambda_client()
        funcs = client.list_functions()["Functions"]
        assert len(funcs) >= 3

        # Concurrency
        client.put_function_concurrency(FunctionName="image-resizer-prod", ReservedConcurrentExecutions=50)
        updated = [f for f in client.list_functions()["Functions"] if f["FunctionName"] == "image-resizer-prod"][0]
        assert updated.get("ReservedConcurrentExecutions") == 50

        # Delete concurrency
        client.delete_function_concurrency(FunctionName="image-resizer-prod")
        updated2 = [f for f in client.list_functions()["Functions"] if f["FunctionName"] == "image-resizer-prod"][0]
        assert "ReservedConcurrentExecutions" not in updated2

    def test_simulated_cloudwatch_client(self):
        client = get_simulated_cloudwatch_client()
        resp = client.get_metric_statistics(
            Dimensions=[{"Name": "InstanceId", "Value": "i-017eef9204bc3811"}]
        )
        datapoint = resp["Datapoints"][0]
        assert datapoint["Average"] >= 80.0  # Spiking instance

    def test_simulated_s3_and_sts(self):
        s3 = get_simulated_s3_client()
        assert len(s3.list_buckets()["Buckets"]) >= 3

        sts = get_simulated_sts_client()
        assert sts.get_caller_identity()["UserId"] == "SIMULATION_MODE_USER"


# ─────────────────────────────────────────────────────────────────────────────
# Test Full Simulation Pipeline End-to-End
# ─────────────────────────────────────────────────────────────────────────────
class TestSimulationPipeline:
    def test_full_pipeline_cycle_and_rollback(self):
        # 1. Seed baseline historical telemetry
        seed_historical_data()
        cost_table = get_table("CostTelemetry")
        assert cost_table.item_count > 0

        # 2. Collector runs against simulated AWS clients
        run_collector()
        assert cost_table.item_count > 28

        # 3. Anomaly detector detects spikes and idle instances
        run_detection()
        anomaly_table = get_table("AnomalyEvents")
        anomalies = anomaly_table.scan()["Items"]
        assert len(anomalies) > 0

        # 4. Optimization engine executes rules and records audit
        run_engine()
        audit_table = get_table("OptimizationAudit")
        audits = audit_table.scan()["Items"]
        assert len(audits) > 0
        actioned = [a for a in audits if a.get("status") == "actioned"]
        assert len(actioned) > 0

        # 5. Rollback restores simulated state
        rollback_audit_actions()
        audits_after = audit_table.scan()["Items"]
        rolled_back = [a for a in audits_after if a.get("status") == "rolled_back"]
        assert len(rolled_back) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Test Dashboard API Endpoints with In-Memory Store
# ─────────────────────────────────────────────────────────────────────────────
class TestDashboardInSimulationMode:
    @pytest.fixture
    def client(self):
        flask_app.config["TESTING"] = True
        with flask_app.test_client() as client:
            yield client

    def test_cost_trend_endpoint(self, client):
        seed_historical_data()
        resp = client.get("/api/cost-trend")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "Amazon EC2" in data
        assert len(data["Amazon EC2"]) > 0

    def test_anomalies_endpoint(self, client):
        seed_historical_data()
        resp = client.get("/api/anomalies")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_optimization_log_endpoint(self, client):
        seed_historical_data()
        resp = client.get("/api/optimization-log")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_savings_summary_endpoint(self, client):
        seed_historical_data()
        resp = client.get("/api/savings-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "total_saving_usd" in data
        assert "actions_taken" in data
        assert data["actions_taken"] > 0

    def test_trigger_pipeline_endpoint(self, client):
        resp = client.post("/api/run-pipeline")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
