"""Explicit, synchronous, sequential invocation of the verified sandbox only."""
import argparse
import json
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

NAME = "costintel-lab-workload"
REGION = "ap-south-1"


def verify_lab(session, expected_account):
    # No SDK retries: an uncertain invocation result must never be replayed.
    config = Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1})
    identity = session.client("sts", region_name=REGION, config=config).get_caller_identity()
    if identity["Account"] != expected_account or identity["Arn"].endswith(":root"):
        raise ValueError("Wrong AWS account or root identity")
    client = session.client("lambda", region_name=REGION, config=config)
    function = client.get_function(FunctionName=NAME)
    settings = function["Configuration"]
    expected_arn = f"arn:aws:lambda:{REGION}:{expected_account}:function:{NAME}"
    if (settings["FunctionArn"] != expected_arn or
            function.get("Tags", {}).get("CostIntelLab") != "true" or
            settings.get("Timeout") != 3 or settings.get("MemorySize") != 128 or
            settings.get("State") != "Active" or settings.get("LastUpdateStatus") != "Successful"):
        raise ValueError("Function does not match the active bounded lab configuration")
    return client, expected_arn


def run(session, expected_account, count=10, scenario="baseline"):
    if not 1 <= count <= 20 or scenario not in {"baseline", "slow", "error"}:
        raise ValueError("Use 1-20 invocations and a supported scenario")
    client, expected_arn = verify_lab(session, expected_account)
    errors = 0
    for _ in range(count):
        response = client.invoke(FunctionName=expected_arn, InvocationType="RequestResponse",
                                 Payload=json.dumps({"scenario": scenario}).encode())
        response["Payload"].close()
        if response.get("StatusCode") != 200:
            raise ValueError("Unexpected invocation status; stopped without retrying")
        failed = bool(response.get("FunctionError"))
        errors += int(failed)
        if failed and scenario != "error":
            raise ValueError("Unexpected function error; stopped without further invocations")
    return {"scenario": scenario, "invocations": count, "function_errors": errors,
            "region": REGION, "function_name": NAME}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--expected-account", required=True)
    parser.add_argument("--count", type=int, default=10, choices=range(1, 21))
    parser.add_argument("--scenario", choices=["baseline", "slow", "error"], default="baseline")
    args = parser.parse_args(argv)
    try:
        result = run(boto3.Session(profile_name=args.profile), args.expected_account, args.count, args.scenario)
    except (BotoCoreError, ClientError, ValueError) as exc:
        code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
        print(f"Lab stopped ({code}). Some invocations may already have executed; do not blindly retry.", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
