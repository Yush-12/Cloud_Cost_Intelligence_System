"""Bounded CloudWatch reads with explicit gaps and per-resource attribution."""
from datetime import datetime, timedelta, timezone
import math


METRICS = {
    "ec2": [("CPUUtilization", "Average", "Percent"),
            ("NetworkIn", "Sum", "Bytes"), ("NetworkOut", "Sum", "Bytes")],
    "lambda": [("Invocations", "Sum", "Count"), ("Errors", "Sum", "Count"),
               ("Duration", "Average", "Milliseconds"),
               ("Throttles", "Sum", "Count"), ("ConcurrentExecutions", "Maximum", "Count")],
    "dynamodb": [("ConsumedReadCapacityUnits", "Sum", "Count"),
                 ("ConsumedWriteCapacityUnits", "Sum", "Count")],
    "s3": [("BucketSizeBytes", "Average", "Bytes")],
}


def collect_metrics(client, resources, now=None, max_resources=10, window_hours=1):
    if not 1 <= max_resources <= 100:
        raise ValueError("max_resources must be between 1 and 100")
    if not 1 <= window_hours <= 48:
        raise ValueError("window_hours must be between 1 and 48")
    now = now or datetime.now(timezone.utc)
    # Query completed five-minute buckets only. Keep original AWS timestamps.
    end = datetime.fromtimestamp(int(now.timestamp()) // 300 * 300, timezone.utc)
    start = end - timedelta(hours=window_hours)
    expected_points = window_hours * 12
    eligible = sorted((r for r in resources if r["service"] in METRICS),
                      key=lambda r: (r["service"], r["resource_id"]))
    selected = eligible[:max_resources]
    series = []
    for resource in selected:
        service = resource["service"]
        period = 86400 if service == "s3" else 300
        metric_end = end
        metric_start = (end - timedelta(days=2)) if service == "s3" else start
        exp_pts = 2 if service == "s3" else expected_points

        dim_name = {
            "ec2": "InstanceId",
            "lambda": "FunctionName",
            "s3": "BucketName",
            "dynamodb": "TableName",
        }[service]
        dim_value = (
            resource["resource_id"].split(":function:", 1)[-1]
            if service == "lambda"
            else resource["resource_id"]
        )
        dimensions = [{"Name": dim_name, "Value": dim_value}]
        if service == "s3":
            dimensions.append({"Name": "StorageType", "Value": "StandardStorage"})

        namespace = {
            "ec2": "AWS/EC2",
            "lambda": "AWS/Lambda",
            "s3": "AWS/S3",
            "dynamodb": "AWS/DynamoDB",
        }[service]

        for name, statistic, unit in METRICS[service]:
            response = client.get_metric_statistics(
                Namespace=namespace,
                MetricName=name,
                Dimensions=dimensions,
                StartTime=metric_start,
                EndTime=metric_end,
                Period=period,
                Statistics=[statistic],
                Unit=unit,
            )
            by_time = {}
            for point in response.get("Datapoints", []):
                timestamp = point["Timestamp"].astimezone(timezone.utc)
                value = point.get(statistic)
                if metric_start <= timestamp < metric_end and value is not None and math.isfinite(value):
                    by_time[timestamp.isoformat()] = float(value)
            points = [{"timestamp": timestamp, "value": value}
                      for timestamp, value in sorted(by_time.items())]
            latest = points[-1] if points else None
            series.append({"resource_id": resource["resource_id"], "service": service,
                           "scope": "StandardStorage only" if service == "s3" else "resource",
                           "metric": name, "statistic": statistic, "unit": unit,
                           "period_seconds": period, "points": points,
                           "status": "available" if points else "missing",
                           "latest": latest, "expected_points": exp_pts,
                           "missing_points": max(0, exp_pts - len(points))})
    return {"window_start": start.isoformat(), "window_end": end.isoformat(),
            "collected_resources": len(selected), "skipped_resources": len(eligible) - len(selected),
            "api_calls": len(series), "series": series}
