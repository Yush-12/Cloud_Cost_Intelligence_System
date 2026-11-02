import json
import pytest
from costintel.inventory import collect_demo
from costintel.store import SnapshotStore
from costintel.web import create_app


@pytest.fixture
def test_client(tmp_path):
    store = SnapshotStore(tmp_path / "test.db")
    data = collect_demo("ap-south-1")
    store.save("local", data)
    app = create_app(store, "local", "demo", "demo-account", "ap-south-1")
    return app.test_client()


def test_login_page_renders(test_client):
    res = test_client.get("/login")
    assert res.status_code == 200
    assert b"Connect AWS Workspace" in res.data
    assert b"RAM-Only Lifecycle" in res.data


def test_auth_login_and_logout(test_client):
    login_res = test_client.post("/api/auth/login", json={"mode": "demo"})
    assert login_res.status_code == 200
    assert login_res.json["status"] == "ok"
    assert login_res.json["account_id"] == "demo-account"

    logout_res = test_client.post("/api/auth/logout")
    assert logout_res.status_code == 200
    assert logout_res.json["status"] == "ok"


def test_api_lab_provision_and_teardown_demo(test_client):
    prov_res = test_client.post("/api/lab/provision")
    assert prov_res.status_code == 200
    assert prov_res.json["status"] == "provisioned"
    assert len(prov_res.json["resources"]) == 4

    tear_res = test_client.post("/api/lab/teardown")
    assert tear_res.status_code == 200
    assert tear_res.json["status"] == "completed"


def test_api_profiles_endpoint(test_client):
    res = test_client.get("/api/profiles")
    assert res.status_code == 200
    data = res.json
    assert "active" in data
    assert "profiles" in data
    assert data["active"]["mode"] == "demo"
    assert any(p["name"] == "demo" for p in data["profiles"])


def test_api_profiles_switch_to_demo(test_client):
    res = test_client.post("/api/profiles/switch", json={"mode": "demo", "profile": "demo"})
    assert res.status_code == 200
    data = res.json
    assert data["status"] == "ok"
    assert data["active"]["mode"] == "demo"
    assert data["active"]["account_id"] == "demo-account"


def test_api_pipeline_run_demo_mode(test_client):
    res = test_client.post("/api/pipeline/run", json={"apply": False, "stimulate": "slow"})
    assert res.status_code == 200
    data = res.json
    assert data["status"] == "success"
    assert "report" in data
    assert "summary" in data
    assert data["stimulation"]["scenario"] == "slow"


def test_api_workload_stimulate_and_restore_demo(test_client):
    res_stim = test_client.post("/api/workload/stimulate", json={"scenario": "slow", "count": 3})
    assert res_stim.status_code == 200
    assert res_stim.json["status"] == "ok"
    assert res_stim.json["result"]["scenario"] == "slow"

    res_rest = test_client.post("/api/workload/restore")
    assert res_rest.status_code == 200
    assert res_rest.json["status"] == "ok"
    assert "baseline restored" in res_rest.json["message"]


def test_session_isolation(tmp_path):
    store = SnapshotStore(tmp_path / "test_iso.db")
    app = create_app(store, "local", "aws", "111122223333", "ap-south-1")
    client_a = app.test_client()
    client_b = app.test_client()

    inv_a = client_a.get("/api/inventory").json
    assert inv_a["account_id"] == "111122223333"
    assert inv_a["mode"] == "aws"

    login_b = client_b.post("/api/auth/login", json={"mode": "demo"}).json
    assert login_b["status"] == "ok"
    assert login_b["account_id"] == "demo-account"

    inv_b = client_b.get("/api/inventory").json
    assert inv_b["account_id"] == "demo-account"
    assert inv_b["mode"] == "demo"

    # Client A must still see original isolated AWS account
    inv_a2 = client_a.get("/api/inventory").json
    assert inv_a2["account_id"] == "111122223333"
    assert inv_a2["mode"] == "aws"


def test_account_scoped_restoration(tmp_path):
    store = SnapshotStore(tmp_path / "test_restore.db")
    with store.connect() as db:
        db.execute("CREATE TABLE containment_actions (id TEXT PRIMARY KEY, plan TEXT, status TEXT, created_at TEXT)")
        db.execute(
            "INSERT INTO containment_actions VALUES (?, ?, ?, ?)",
            ("act-aws", json.dumps({"account": "695313591289", "resource": "arn:aws:lambda:ap-south-1:695313591289:function:lab"}), "contained", "2026-11-01T00:00:00Z")
        )
        db.execute(
            "INSERT INTO containment_actions VALUES (?, ?, ?, ?)",
            ("act-demo", json.dumps({"account": "demo-account", "resource": "demo-lambda-workload"}), "contained", "2026-11-01T00:00:00Z")
        )

    app = create_app(store, "local", "demo", "demo-account", "ap-south-1")
    client = app.test_client()

    res = client.post("/api/workload/restore")
    assert res.status_code == 200

    with store.connect() as db:
        demo_status = db.execute("SELECT status FROM containment_actions WHERE id='act-demo'").fetchone()[0]
        aws_status = db.execute("SELECT status FROM containment_actions WHERE id='act-aws'").fetchone()[0]

    assert demo_status == "restored"
    assert aws_status == "contained"
