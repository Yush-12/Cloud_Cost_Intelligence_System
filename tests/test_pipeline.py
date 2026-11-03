from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
import pytest

from costintel.pipeline import run_pipeline, format_summary
from costintel.store import SnapshotStore


def test_pipeline_demo_mode(tmp_path):
    store = SnapshotStore(tmp_path / "pipeline.db")
    report = run_pipeline(store, mode="demo", account="demo-account", region="ap-south-1")
    assert report["mode"] == "demo"
    assert report["inventory"]["resources_count"] == 4
    assert report["execution"]["mode"] == "dry_run"
    assert "snapshot_id" in report

    summary = format_summary(report)
    assert "CostIntel Pipeline Run Summary" in summary
    assert "DEMO MODE" in summary


def test_pipeline_aws_dry_run_and_apply(tmp_path):
    store = SnapshotStore(tmp_path / "pipeline.db")
    account, region, inst_id = "123456789012", "ap-south-1", "i-1234567890abcdef0"
    
    # Pre-populate snapshot with an idle EC2 instance and CPU metrics
    snap = {
        "mode": "aws", "account_id": account, "region": region,
        "resources": [{"resource_id": inst_id, "service": "ec2", "state": "running", "instance_type": "t2.micro"}],
        "telemetry": {"series": [{
            "resource_id": inst_id, "service": "ec2", "metric": "CPUUtilization",
            "statistic": "Average", "unit": "Percent", "period_seconds": 300,
            "points": [{"timestamp":(datetime.now(timezone.utc).replace(second=0,microsecond=0)-timedelta(minutes=5*m)).isoformat(),"value":1.1} for m in range(12,0,-1)]
        }]}
    }
    store.save("local", snap)

    mock_session = Mock()
    mock_sts = Mock()
    mock_ec2 = Mock()
    mock_lambda = Mock()
    mock_cw = Mock()

    mock_session.client.side_effect = lambda name, **kw: (
        mock_sts if name == "sts" else
        mock_ec2 if name == "ec2" else
        mock_lambda if name == "lambda" else
        mock_cw
    )
    mock_sts.get_caller_identity.return_value = {"Account": account, "Arn": f"arn:aws:iam::{account}:user/test"}
    mock_ec2.get_paginator.return_value.paginate.return_value = [{"Reservations": [{"Instances": [{
        "InstanceId": inst_id, "State": {"Name": "running"}, "InstanceType": "t2.micro"
    }]}]}]
    mock_lambda.get_paginator.return_value.paginate.return_value = [{"Functions": []}]
    mock_cw.get_metric_statistics.return_value = {"Datapoints": []}
    mock_ec2.describe_instances.return_value = {
        "Reservations": [{"Instances": [{
            "InstanceId": inst_id, "State": {"Name": "running"},
            "Tags": [{"Key": "CostIntelLab", "Value": "true"}, {"Key": "AutoOptimize", "Value": "true"}, {"Key":"CostIntelIdleStop","Value":"true"}]
        }]}]
    }
    mock_ec2.stop_instances.return_value = {"StoppingInstances": [{"InstanceId": inst_id, "CurrentState": {"Name": "stopping"}}]}

    instance=mock_ec2.describe_instances.return_value['Reservations'][0]['Instances'][0]
    mock_ec2.get_waiter.return_value.wait.side_effect=lambda **kw: instance['State'].update(Name='stopped')

    # Test dry run
    with patch("boto3.Session", return_value=mock_session):
        dry_report = run_pipeline(
            store, mode="aws", account=account, region=region, profile="test-profile",
            with_metrics=False, apply=False, action_type="stop-ec2"
        )
        assert dry_report["execution"]["mode"] == "dry_run"
        assert len(dry_report["plans_generated"]) == 1
        assert dry_report["plans_generated"][0]["action"] == "stop_idle_instance"

        # Test apply
        apply_report = run_pipeline(
            store, mode="aws", account=account, region=region, profile="test-profile",
            with_metrics=False, apply=True, action_type="stop-ec2"
        )
        assert apply_report["execution"]["mode"] == "applied"
        assert len(apply_report["execution"]["results"]) == 1
        assert apply_report["execution"]["results"][0]["status"] == "applied"
        mock_ec2.stop_instances.assert_called_once_with(InstanceIds=[inst_id])

