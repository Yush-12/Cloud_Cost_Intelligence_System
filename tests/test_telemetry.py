from tests.web_helpers import connect
from datetime import datetime, timedelta, timezone

import boto3
from botocore.stub import Stubber
import pytest

from costintel.telemetry import collect_metrics, METRICS
from costintel.inventory import snapshot
from costintel.store import SnapshotStore
from costintel.web import create_app


NOW = datetime(2026, 10, 31, 12, 2, tzinfo=timezone.utc)
END = NOW.replace(minute=0)
RESOURCE = {"service": "ec2", "resource_id": "i-example", "state": "running"}


def client():
    return boto3.client("cloudwatch", region_name="ap-south-1",
                        aws_access_key_id="testing", aws_secret_access_key="testing")


def params(name, statistic, unit, service="ec2", value="i-example"):
    return {"Namespace": "AWS/EC2" if service == "ec2" else "AWS/Lambda", "MetricName": name,
            "Dimensions": [{"Name": "InstanceId" if service == "ec2" else "FunctionName", "Value": value}],
            "StartTime": END - timedelta(hours=1), "EndTime": END,
            "Period": 300, "Statistics": [statistic], "Unit": unit}


def test_sorts_samples_and_keeps_missing_distinct_from_zero():
    cw = client()
    with Stubber(cw) as stub:
        stub.add_response("get_metric_statistics", {"Datapoints": [
            {"Timestamp": END - timedelta(minutes=5), "Average": 0.0},
            {"Timestamp": END - timedelta(minutes=10), "Average": 2.0},
        ]}, params("CPUUtilization", "Average", "Percent"))
        for name in ("NetworkIn", "NetworkOut"):
            stub.add_response("get_metric_statistics", {"Datapoints": []}, params(name, "Sum", "Bytes"))
        result = collect_metrics(cw, [RESOURCE], now=NOW)
        cpu, incoming, outgoing = result["series"]
        assert [p["value"] for p in cpu["points"]] == [2.0, 0.0]
        assert cpu["latest"]["value"] == 0.0
        assert cpu["missing_points"] == 10
        assert incoming["latest"] is None
        assert incoming["status"] == "missing"
        assert outgoing["points"] == []
        stub.assert_no_pending_responses()


def test_viewer_renders_real_zero_and_missing_separately(tmp_path):
    cw = client()
    with Stubber(cw) as stub:
        for name, statistic, unit in METRICS["ec2"]:
            points = ([{"Timestamp": END - timedelta(minutes=5), statistic: 0.0}]
                      if name == "CPUUtilization" else [])
            stub.add_response("get_metric_statistics", {"Datapoints": points}, params(name, statistic, unit))
        telemetry = collect_metrics(cw, [RESOURCE], now=NOW)
    data = snapshot("aws", "123456789012", "ap-south-1", [RESOURCE])
    data["telemetry"] = telemetry
    store = SnapshotStore(tmp_path / "data.db")
    store.save("local", data)
    viewer = create_app(store, "local", "aws", "123456789012", "ap-south-1").test_client()
    connect(viewer)
    html = viewer.get("/").get_data(as_text=True)
    assert "0.0 Percent" in html
    assert "Missing — no datapoints" in html
    assert "1/12 buckets" in html


def test_lambda_dimensions_statistics_and_resource_limit():
    cw = client()
    resource = {"service": "lambda", "resource_id": "arn:aws:lambda:ap-south-1:123456789012:function:demo"}
    with Stubber(cw) as stub:
        for name, statistic, unit in METRICS["lambda"]:
            stub.add_response("get_metric_statistics", {"Datapoints": []},
                              params(name, statistic, unit, "lambda", "demo"))
        result = collect_metrics(cw, [resource, {**resource, "resource_id": resource["resource_id"] + "z"}],
                                 now=NOW, max_resources=1)
        assert result["collected_resources"] == 1
        assert result["skipped_resources"] == 1
        assert result["api_calls"] == 5
        assert all(s["resource_id"] == resource["resource_id"] for s in result["series"])
        stub.assert_no_pending_responses()


def test_empty_account_needs_no_cloudwatch_calls():
    with Stubber(client()) as stub:
        result = collect_metrics(stub.client, [], now=NOW)
        assert result["api_calls"] == 0
        assert result["series"] == []


def test_historical_window_expands_coverage_without_invocations():
    with Stubber(client()) as stub:
        result = collect_metrics(stub.client, [], now=NOW, window_hours=48)
        assert result["window_start"] == (END - timedelta(hours=48)).isoformat()
        assert result["api_calls"] == 0


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_invalid_collection_limits(limit):
    with pytest.raises(ValueError):
        collect_metrics(None, [], now=NOW, max_resources=limit)


def sample():
    data = snapshot("aws", "123456789012", "ap-south-1", [RESOURCE])
    data["telemetry"] = {"series": [{"resource_id": "i-example", "metric": "CPUUtilization",
        "statistic": "Average", "unit": "Percent", "period_seconds": 300,
        "points": [{"timestamp": END.isoformat(), "value": 0.5}]}]}
    return data


def test_history_is_durable_deduplicated_and_scoped(tmp_path):
    path = tmp_path / "data.db"
    store = SnapshotStore(path)
    data = sample()
    store.save("alice", data)
    data["telemetry"]["series"][0]["points"][0]["value"] = 0.75
    store.save("alice", data)
    store = SnapshotStore(path)
    args = ("alice", "aws", "123456789012", "ap-south-1", "i-example", "CPUUtilization")
    points = store.history(*args)
    assert len(points) == 1
    assert points[0]["value"] == 0.75
    for index, value in enumerate(("bob", "demo", "other", "us-east-1", "i-other", "NetworkIn")):
        other = list(args)
        other[index] = value
        assert store.history(*other) == []


def test_snapshot_and_observations_are_atomic(tmp_path):
    store = SnapshotStore(tmp_path / "data.db")
    data = sample()
    data["telemetry"]["series"].append({"points": [{}]})
    with pytest.raises(KeyError):
        store.save("local", data)
    assert store.latest("local", "aws", "123456789012", "ap-south-1") is None
    assert store.history("local", "aws", "123456789012", "ap-south-1", "i-example", "CPUUtilization") == []


def test_metrics_endpoint_cannot_change_account_scope(tmp_path):
    store = SnapshotStore(tmp_path / "data.db")
    store.save("other-owner", sample())
    test_client = create_app(store, "local", "aws", "123456789012", "ap-south-1").test_client()
    connect(test_client)
    assert test_client.get("/api/metrics").status_code == 400
    result = test_client.get("/api/metrics?resource_id=i-example&metric=CPUUtilization&owner=other-owner").json
    assert result["points"] == []
