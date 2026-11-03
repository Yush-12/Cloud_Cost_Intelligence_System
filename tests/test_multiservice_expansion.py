from datetime import datetime, timezone
from unittest.mock import Mock, MagicMock
from botocore.exceptions import ClientError

from costintel.inventory import collect_aws, annotate_resource_cost
from costintel.telemetry import collect_metrics, METRICS
from costintel.actions import idle_ec2_plan
from costintel.store import SnapshotStore


def test_inventory_warning_reports_aws_error_code_without_leaking_message():
    session = Mock()
    clients = {name: Mock() for name in ('sts', 'ec2', 'lambda', 's3', 'dynamodb')}
    session.client.side_effect = lambda name, **kwargs: clients[name]
    clients['sts'].get_caller_identity.return_value = {
        'Account': '123456789012', 'Arn': 'arn:aws:iam::123456789012:user/reader'}
    clients['ec2'].get_paginator.side_effect = lambda name: (
        Mock(paginate=Mock(return_value=[{'Reservations': []}])) if name == 'describe_instances'
        else Mock(paginate=Mock(side_effect=ClientError(
            {'Error': {'Code': 'AccessDenied', 'Message': 'private diagnostic'}}, 'DescribeVolumes'))))
    clients['lambda'].get_paginator.return_value.paginate.return_value = [{'Functions': []}]
    clients['s3'].can_paginate.return_value = False
    clients['s3'].list_buckets.return_value = {'Buckets': []}
    clients['dynamodb'].get_paginator.return_value.paginate.return_value = [{'TableNames': []}]

    data = collect_aws('ap-south-1', session=session)
    assert data['warnings'] == [{'service': 'ebs', 'code': 'AccessDenied',
                                 'required_reads': ['ec2:DescribeVolumes'],
                                 'message': 'Inventory incomplete; check read permissions and availability.'}]
    assert 'private diagnostic' not in str(data)


def test_annotate_resource_cost_ebs_and_dynamodb():
    ebs_res = {"resource_id": "vol-12345", "service": "ebs", "size_gb": 100, "state": "available"}
    annotated_ebs = annotate_resource_cost(ebs_res)
    assert annotated_ebs["est_monthly_usd"] is None  # 100 GB * $0.08/GB-mo
    assert "Not estimated" in annotated_ebs["rate_display"]

    ddb_res = {"resource_id": "analytics-table", "service": "dynamodb", "billing_mode": "PAY_PER_REQUEST"}
    annotated_ddb = annotate_resource_cost(ddb_res)
    assert annotated_ddb["est_monthly_usd"] is None
    assert "Not estimated" in annotated_ddb["rate_display"]


def test_collect_aws_multi_service_discovery():
    session = Mock()
    sts_client = Mock()
    ec2_client = Mock()
    lambda_client = Mock()
    s3_client = Mock()
    ddb_client = Mock()

    def client_factory(service_name, **kwargs):
        if service_name == "sts":
            return sts_client
        if service_name == "ec2":
            return ec2_client
        if service_name == "lambda":
            return lambda_client
        if service_name == "s3":
            return s3_client
        if service_name == "dynamodb":
            return ddb_client
        return Mock()

    session.client.side_effect = client_factory

    sts_client.get_caller_identity.return_value = {
        "Account": "695313591289",
        "Arn": "arn:aws:iam::695313591289:user/finops-test",
    }

    # EC2 Instances paginator mock
    ec2_inst_paginator = Mock()
    ec2_inst_paginator.paginate.return_value = [{
        "Reservations": [{
            "Instances": [{
                "InstanceId": "i-0a1b2c3d4e",
                "State": {"Name": "running"},
                "InstanceType": "t2.micro",
            }]
        }]
    }]

    # EBS Volumes paginator mock
    ec2_vol_paginator = Mock()
    ec2_vol_paginator.paginate.return_value = [{
        "Volumes": [{
            "VolumeId": "vol-99887766",
            "State": "available",
            "Attachments": [],
            "Size": 25,
        }]
    }]

    def get_ec2_paginator(operation):
        if operation == "describe_instances":
            return ec2_inst_paginator
        if operation == "describe_volumes":
            return ec2_vol_paginator
        return Mock()

    ec2_client.get_paginator.side_effect = get_ec2_paginator

    # Lambda list_functions paginator mock
    lambda_paginator = Mock()
    lambda_paginator.paginate.return_value = [{
        "Functions": [{
            "FunctionArn": "arn:aws:lambda:ap-south-1:695313591289:function:worker",
            "Runtime": "python3.12",
            "MemorySize": 128,
        }]
    }]
    lambda_client.get_paginator.return_value = lambda_paginator

    # S3 list_buckets + get_bucket_location
    s3_client.list_buckets.return_value = {
        "Buckets": [
            {"Name": "my-mumbai-bucket"},
            {"Name": "my-us-bucket"},
        ]
    }
    s3_client.get_bucket_location.side_effect = lambda Bucket: (
        {"LocationConstraint": "ap-south-1"} if Bucket == "my-mumbai-bucket"
        else {"LocationConstraint": "us-west-2"}
    )

    # DynamoDB list_tables + describe_table
    ddb_client.get_paginator.return_value.paginate.return_value = [{"TableNames": ["cache-table"]}]
    ddb_client.describe_table.return_value = {
        "Table": {
            "TableStatus": "ACTIVE",
            "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"},
        }
    }

    data = collect_aws("ap-south-1", session=session, expected_account="695313591289", with_metrics=False)

    assert data["account_id"] == "695313591289"
    assert data["region"] == "ap-south-1"

    res_by_service = {}
    for r in data["resources"]:
        res_by_service.setdefault(r["service"], []).append(r)

    assert "ec2" in res_by_service
    assert res_by_service["ec2"][0]["resource_id"] == "i-0a1b2c3d4e"

    assert "lambda" in res_by_service
    assert res_by_service["lambda"][0]["memory_mb"] == 128

    assert "ebs" in res_by_service
    assert res_by_service["ebs"][0]["resource_id"] == "vol-99887766"
    assert res_by_service["ebs"][0]["size_gb"] == 25
    assert res_by_service["ebs"][0]["est_monthly_usd"] is None

    # Verify S3 region filtering kept only the ap-south-1 bucket
    assert "s3" in res_by_service
    assert len(res_by_service["s3"]) == 1
    assert res_by_service["s3"][0]["resource_id"] == "my-mumbai-bucket"

    # Verify DynamoDB discovery
    assert "dynamodb" in res_by_service
    assert res_by_service["dynamodb"][0]["resource_id"] == "cache-table"
    assert res_by_service["dynamodb"][0]["billing_mode"] == "PAY_PER_REQUEST"


def test_collect_metrics_dynamodb_and_s3():
    cw = Mock()
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)

    resources = [
        {"service": "dynamodb", "resource_id": "test-table", "state": "ACTIVE"},
        {"service": "s3", "resource_id": "test-bucket", "state": "available"},
    ]

    cw.get_metric_statistics.return_value = {
        "Datapoints": [
            {"Timestamp": now, "Sum": 12.0, "Average": 12.0}
        ]
    }

    result = collect_metrics(cw, resources, now=now, max_resources=10)

    assert result["collected_resources"] == 2
    assert len(result["series"]) == 3  # 2 for DynamoDB (Read/Write) + 1 for S3 (BucketSizeBytes)

    ddb_series = [s for s in result["series"] if s["service"] == "dynamodb"]
    assert len(ddb_series) == 2
    assert {s["metric"] for s in ddb_series} == {"ConsumedReadCapacityUnits", "ConsumedWriteCapacityUnits"}
    assert all(s["period_seconds"] == 300 for s in ddb_series)

    s3_series = [s for s in result["series"] if s["service"] == "s3"]
    assert len(s3_series) == 1
    assert s3_series[0]["metric"] == "BucketSizeBytes"
    assert s3_series[0]["period_seconds"] == 86400  # Daily metric


def test_idle_ec2_plan_validation_rules(tmp_path):
    store = SnapshotStore(tmp_path / "ec2_test.db")
    account = "695313591289"
    region = "ap-south-1"

    # Save snapshot with 1 running EC2 instance
    snap = {
        "mode": "aws",
        "account_id": account,
        "region": region,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "coverage": ["ec2"],
        "resources": [{"resource_id": "i-testidle", "service": "ec2", "state": "running"}]
    }

    # Case 1: Less than 3 points -> Plan should NOT be generated
    snap["telemetry"] = {
        "series": [{
            "resource_id": "i-testidle",
            "metric": "CPUUtilization",
            "statistic": "Average",
            "unit": "Percent",
            "period_seconds": 300,
            "points": [
                {"timestamp": "2026-11-02T10:00:00Z", "value": 0.5},
                {"timestamp": "2026-11-02T10:05:00Z", "value": 0.4},
            ]
        }]
    }
    store.save("local", snap)
    assert idle_ec2_plan(store, account, region) == []

    # Case 2: Negative or out-of-range point -> Plan should NOT be generated
    snap["telemetry"]["series"][0]["points"] = [
        {"timestamp": "2026-11-02T10:00:00Z", "value": 0.5},
        {"timestamp": "2026-11-02T10:05:00Z", "value": -1.0},
        {"timestamp": "2026-11-02T10:10:00Z", "value": 0.4},
    ]
    store.save("local", snap)
    assert idle_ec2_plan(store, account, region) == []

    # Case 3: 3 valid low CPU points (<= 2.0%) -> Plan IS generated
    snap["telemetry"]["series"][0]["points"] = [
        {"timestamp": "2026-11-02T10:00:00Z", "value": 0.8},
        {"timestamp": "2026-11-02T10:05:00Z", "value": 0.6},
        {"timestamp": "2026-11-02T10:10:00Z", "value": 0.4},
    ]
    store.save("local", snap)
    plans = idle_ec2_plan(store, account, region)
    assert plans == []  # Three stale observations cannot authorize shutdown.
