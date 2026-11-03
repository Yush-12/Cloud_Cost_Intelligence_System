from tests.web_helpers import connect
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from botocore.stub import Stubber

from costintel.inventory import collect_aws, collect_demo, snapshot
from costintel.store import SnapshotStore
from costintel.web import create_app


def test_persistence_and_scope(tmp_path):
    path = tmp_path / "inventory.db"
    store = SnapshotStore(path)
    data = collect_demo("ap-south-1")
    store.save("alice", data)
    reopened = SnapshotStore(path)
    assert reopened.latest("alice", "demo", "demo-account", "ap-south-1") == data
    for scope in [("bob", "demo", "demo-account", "ap-south-1"),
                  ("alice", "aws", "demo-account", "ap-south-1"),
                  ("alice", "demo", "other", "ap-south-1"),
                  ("alice", "demo", "demo-account", "us-east-1")]:
        assert reopened.latest(*scope) is None


def test_viewer_distinguishes_uncollected_empty_and_stale(tmp_path):
    store = SnapshotStore(tmp_path / "inventory.db")
    client = create_app(store, "local", "aws", "123456789012", "ap-south-1").test_client()
    connect(client)
    assert client.get("/api/inventory").json["status"] == "not_collected"
    data = snapshot("aws", "123456789012", "ap-south-1", [])
    data["collected_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    store.save("local", data)
    result = client.get("/api/inventory?account=other&mode=demo").json
    assert result["status"] == "snapshot_available"
    assert result["snapshot"]["resources"] == []
    assert result["account_id"] == "123456789012"
    assert result["stale"] is True
    assert result["optimization_enabled"] is False
    assert "est_monthly_savings" in result
    page = client.get("/").data
    assert b"Collection succeeded" in page
    assert b"Evidence, before estimates." in page
    assert b"Billing not connected" in page
    actions_res = client.get("/api/actions").json
    assert "reviews" in actions_res
    assert "containments" in actions_res
    assert "optimizations" in actions_res
    assert client.post("/api/run-pipeline").status_code == 404


@pytest.fixture
def aws_session():
    # Explicit fake credentials and Stubber prevent network/credential discovery.
    session = boto3.Session(aws_access_key_id="testing", aws_secret_access_key="testing",
                            region_name="ap-south-1")
    clients = {name: session.client(name) for name in ("sts", "ec2", "lambda")}
    class Session:
        def client(self, name, **kwargs):
            return clients[name]
    return Session(), clients


def test_aws_paginates_inventory(aws_session):
    session, clients = aws_session
    with Stubber(clients["sts"]) as sts, Stubber(clients["ec2"]) as ec2, Stubber(clients["lambda"]) as functions:
        sts.add_response("get_caller_identity", {"Account": "123456789012"}, {})
        ec2.add_response("describe_instances", {"Reservations": [], "NextToken": "next"}, {})
        ec2.add_response("describe_instances", {"Reservations": [{"Instances": [
            {"InstanceId": "i-example", "InstanceType": "t3.micro", "State": {"Name": "stopped"}}
        ]}]}, {"NextToken": "next"})
        functions.add_response("list_functions", {"Functions": []}, {})
        data = collect_aws("ap-south-1", expected_account="123456789012", session=session)
        assert data["mode"] == "aws"
        assert data["resources"][0]["resource_id"] == "i-example"
        for stub in (sts, ec2, functions):
            stub.assert_no_pending_responses()


def test_wrong_account_stops_before_inventory(aws_session):
    session, clients = aws_session
    with Stubber(clients["sts"]) as sts, Stubber(clients["ec2"]), Stubber(clients["lambda"]):
        sts.add_response("get_caller_identity", {"Account": "123456789012"}, {})
        with pytest.raises(ValueError, match="does not match"):
            collect_aws("ap-south-1", expected_account="999999999999", session=session)


def test_root_credentials_rejected_before_inventory(aws_session):
    session, clients = aws_session
    with Stubber(clients["sts"]) as sts, Stubber(clients["ec2"]), Stubber(clients["lambda"]):
        sts.add_response("get_caller_identity", {"Account": "123456789012",
                          "Arn": "arn:aws:iam::123456789012:root"}, {})
        with pytest.raises(ValueError, match="Root credentials"):
            collect_aws("ap-south-1", session=session)


def test_collection_failure_does_not_replace_snapshot(tmp_path, monkeypatch, capsys):
    from botocore.exceptions import ClientError
    from costintel import __main__ as cli
    path = tmp_path / "inventory.db"
    store = SnapshotStore(path)
    old = snapshot("aws", "123456789012", "ap-south-1", [])
    store.save("local", old)
    def denied(*args, **kwargs):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "private detail"}}, "DescribeInstances")
    monkeypatch.setattr(cli, "collect_aws", denied)
    assert cli.main(["collect", "--mode", "aws", "--database", str(path)]) == 1
    assert store.latest("local", "aws", "123456789012", "ap-south-1") == old
    assert "private detail" not in capsys.readouterr().err


def test_html_escapes_resource_names(tmp_path):
    store = SnapshotStore(tmp_path / "inventory.db")
    data = collect_demo("ap-south-1")
    data["resources"][0]["resource_id"] = "<script>alert(1)</script>"
    store.save("local", data)
    client = create_app(store, "local", "demo", "demo-account", "ap-south-1").test_client()
    connect(client, mode='demo')
    store.save('local', data)
    html = client.get("/").data
    assert b"<script>alert(1)</script>" not in html
    assert b"&lt;script&gt;" in html
