import importlib.util
import io
from pathlib import Path
from unittest.mock import Mock

import pytest
from scripts.run_lambda_lab import run

ACCOUNT = "123456789012"
ARN = f"arn:aws:lambda:ap-south-1:{ACCOUNT}:function:costintel-lab-workload"


def setup_session():
    session, sts, client = Mock(), Mock(), Mock()
    session.client.side_effect = lambda name, **kwargs: sts if name == "sts" else client
    sts.get_caller_identity.return_value = {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:user/lab"}
    client.get_function.return_value = {"Configuration": {
        "FunctionArn": ARN, "Timeout": 3, "MemorySize": 128,
        "State": "Active", "LastUpdateStatus": "Successful"}, "Tags": {"CostIntelLab": "true"}}
    client.invoke.side_effect = lambda **kwargs: {"StatusCode": 200, "Payload": io.BytesIO(b'{}')}
    return session, sts, client


def test_bounded_invocations_are_synchronous_without_retries():
    session, _, client = setup_session()
    assert run(session, ACCOUNT, 3)["invocations"] == 3
    assert client.invoke.call_count == 3
    assert all(c.kwargs["InvocationType"] == "RequestResponse" for c in client.invoke.call_args_list)
    assert all(c.kwargs["FunctionName"] == ARN for c in client.invoke.call_args_list)
    assert session.client.call_args.kwargs["config"].retries["total_max_attempts"] == 1


@pytest.mark.parametrize("count", [0, 21, -1])
def test_bad_count_never_contacts_aws(count):
    session = Mock()
    with pytest.raises(ValueError):
        run(session, ACCOUNT, count)
    session.client.assert_not_called()


def test_wrong_account_never_invokes():
    session, _, client = setup_session()
    with pytest.raises(ValueError):
        run(session, "999999999999")
    client.get_function.assert_not_called()
    client.invoke.assert_not_called()


def test_root_never_invokes():
    session, sts, client = setup_session()
    sts.get_caller_identity.return_value["Arn"] = f"arn:aws:iam::{ACCOUNT}:root"
    with pytest.raises(ValueError):
        run(session, ACCOUNT)
    client.invoke.assert_not_called()


@pytest.mark.parametrize("field,value", [("Timeout", 30), ("MemorySize", 1024),
    ("FunctionArn", "arn:wrong"), ("State", "Pending"), ("LastUpdateStatus", "InProgress")])
def test_changed_function_never_invoked(field, value):
    session, _, client = setup_session()
    client.get_function.return_value["Configuration"][field] = value
    with pytest.raises(ValueError):
        run(session, ACCOUNT)
    client.invoke.assert_not_called()


def test_untagged_resource_never_invoked():
    session, _, client = setup_session()
    client.get_function.return_value["Tags"] = {}
    with pytest.raises(ValueError):
        run(session, ACCOUNT)
    client.invoke.assert_not_called()


def test_unexpected_function_error_stops_after_one_call():
    session, _, client = setup_session()
    client.invoke.side_effect = lambda **kwargs: {"StatusCode": 200, "FunctionError": "Unhandled", "Payload": io.BytesIO(b'{}')}
    with pytest.raises(ValueError):
        run(session, ACCOUNT, 10)
    assert client.invoke.call_count == 1
    client.invoke.reset_mock()
    assert run(session, ACCOUNT, 2, "error")["function_errors"] == 2


def test_network_failure_is_not_retried():
    from botocore.exceptions import ReadTimeoutError
    session, _, client = setup_session()
    client.invoke.side_effect = ReadTimeoutError(endpoint_url="https://lambda.example")
    with pytest.raises(ReadTimeoutError):
        run(session, ACCOUNT)
    assert client.invoke.call_count == 1


def test_handler_has_only_fixed_delays(monkeypatch):
    path = Path(__file__).parents[1] / "infra/lambda-lab/workload.py"
    spec = importlib.util.spec_from_file_location("lab_workload", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sleep = Mock()
    monkeypatch.setattr(module.time, "sleep", sleep)
    assert module.handler({"scenario": "slow", "duration": 999999}, None)["delay_ms"] == 500
    sleep.assert_called_once_with(0.5)
    with pytest.raises(RuntimeError):
        module.handler({"scenario": "error"}, None)
    with pytest.raises(ValueError):
        module.handler({"scenario": "unknown"}, None)
