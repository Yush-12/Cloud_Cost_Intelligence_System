"""Explicit providers. AWS collection never falls back to demonstration data."""
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def annotate_resource_cost(resource):
    resource['est_monthly_usd'] = None
    resource['rate_display'] = 'Not estimated'
    resource['savings_opportunity'] = 'Inspect usage evidence'
    return resource


def collect_aws(region, profile=None, expected_account=None, session=None,
                with_metrics=False, max_resources=10, window_hours=1):
    session = session or boto3.Session(profile_name=profile, region_name=region)
    config = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2})
    identity = session.client("sts", region_name=region, endpoint_url=f"https://sts.{region}.amazonaws.com", config=config).get_caller_identity()
    if identity.get("Arn", "").endswith(":root"):
        raise ValueError("Root credentials are not supported; use a dedicated read-only identity")
    account = identity["Account"]
    if expected_account and account != expected_account:
        raise ValueError("AWS account does not match --expected-account; collection stopped")
    resources = []
    warnings = []
    coverage = ['ec2','lambda']
    def warn(service):
        warnings.append({'service':service,'message':'Inventory incomplete; check read permissions and availability.'})
    # Core compute inventory
    ec2 = session.client("ec2", region_name=region, config=config)
    for page in ec2.get_paginator("describe_instances").paginate():
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                resources.append({"resource_id": instance["InstanceId"], "service": "ec2",
                                  "state": instance["State"]["Name"],
                                  "instance_type": instance["InstanceType"]})
    # Serverless functions
    functions = session.client("lambda", region_name=region, config=config)
    for page in functions.get_paginator("list_functions").paginate():
        for function in page.get("Functions", []):
            resources.append({"resource_id": function["FunctionArn"], "service": "lambda",
                               "runtime": function.get("Runtime"),
                               "memory_mb": function["MemorySize"]})
    # Storage volumes (EBS)
    try:
        for page in ec2.get_paginator("describe_volumes").paginate():
            for volume in page.get("Volumes", []):
                resources.append({
                    "resource_id": volume["VolumeId"],
                    "service": "ebs",
                    "state": volume.get("State", "available"),
                    "attachments": len(volume.get("Attachments", [])),
                    "size_gb": volume.get("Size", 8),
                })
        coverage.append('ebs')
    except Exception:
        warn('ebs')

    # Optional/safe read: Storage buckets (S3)
    try:
        s3 = session.client("s3", region_name=region, config=config)
        bucket_pages=s3.get_paginator('list_buckets').paginate() if s3.can_paginate('list_buckets') is True else [s3.list_buckets()]
        for bucket in (bucket for page in bucket_pages for bucket in page.get('Buckets',[])):
            try:
                loc = s3.get_bucket_location(Bucket=bucket["Name"]).get("LocationConstraint") or "us-east-1"
                if loc == "EU":
                    loc = "eu-west-1"
                if loc == region:
                    resources.append({"resource_id": bucket["Name"], "service": "s3", "state": "available"})
            except Exception:
                warn("s3")
        coverage.append("s3")
    except Exception:
        warn("s3")

    # Optional/safe read: Free-tier database (DynamoDB)
    try:
        ddb = session.client("dynamodb", region_name=region, config=config)
        table_pages=ddb.get_paginator('list_tables').paginate()
        for table_name in (name for page in table_pages for name in page.get('TableNames',[])):
            try:
                desc = ddb.describe_table(TableName=table_name)["Table"]
                resources.append({
                    "resource_id": table_name,
                    "service": "dynamodb",
                    "state": desc.get("TableStatus", "ACTIVE"),
                    "billing_mode": desc.get("BillingModeSummary", {}).get("BillingMode", "PAY_PER_REQUEST"),
                })
            except Exception:
                warn("dynamodb")
        coverage.append("dynamodb")
    except Exception:
        warn("dynamodb")

    data = snapshot("aws", account, region, resources)
    data.update(coverage=coverage, warnings=warnings)
    if with_metrics:
        from costintel.telemetry import collect_metrics
        data["telemetry"] = collect_metrics(
            session.client("cloudwatch", region_name=region, config=config),
            resources, max_resources=max_resources, window_hours=window_hours)
        data["collected_at"] = datetime.now(timezone.utc).isoformat()
    return data


def snapshot(mode, account, region, resources):
    annotated = [annotate_resource_cost(dict(r)) for r in resources]
    return {"mode": mode, "account_id": account, "region": region,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "coverage": ["ec2", "lambda", "s3", "dynamodb"], "resources": annotated}


def collect_demo(region):
    data = snapshot("demo", "demo-account", region, [
        {"resource_id": "demo-ec2-workload", "service": "ec2", "state": "running",
         "instance_type": "t2.micro"},
        {"resource_id": "demo-lambda-workload", "service": "lambda", "runtime": "python3.12",
         "memory_mb": 128},
        {"resource_id": "costintel-telemetry-archive", "service": "s3", "state": "available",
         "storage_class": "STANDARD"},
        {"resource_id": "costintel-billing-cache", "service": "dynamodb", "state": "ACTIVE",
         "billing_mode": "PAY_PER_REQUEST"},
    ])

    # Explicitly synthetic demo observations; this path never calls AWS.
    end=datetime.fromtimestamp(int(datetime.now(timezone.utc).timestamp())//300*300,timezone.utc)
    series=[]
    for resource,metric,unit,base in [('demo-lambda-workload','Duration','Milliseconds',8.0),('demo-ec2-workload','CPUUtilization','Percent',12.0)]:
        points=[{'timestamp':(end-timedelta(minutes=5*(37-i))).isoformat(),'value':round(base+(i%7)*0.19+(i%3)*0.11,3)} for i in range(37)]
        if metric=='Duration':
            points[-1]['value']=501.8
        series.append({'resource_id':resource,'service':'lambda' if metric=='Duration' else 'ec2','metric':metric,'statistic':'Average','unit':unit,'period_seconds':300,'points':points,'latest':points[-1],'expected_points':37,'missing_points':0,'status':'available','scope':'synthetic demo'})
    data['telemetry']={'window_start':series[0]['points'][0]['timestamp'],'window_end':end.isoformat(),'collected_resources':2,'skipped_resources':0,'api_calls':0,'series':series,'synthetic':True}
    return data
