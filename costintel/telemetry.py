"""Bounded CloudWatch reads with explicit gaps and per-resource attribution."""
from datetime import datetime, timedelta, timezone
import math


METRICS = {
    "ec2": [("CPUUtilization", "Average", "Percent"),
            ("NetworkIn", "Sum", "Bytes"), ("NetworkOut", "Sum", "Bytes")],
    "lambda": [("Invocations", "Sum", "Count"), ("Errors", "Sum", "Count"),
               ("Duration", "Average", "Milliseconds"),
               ("Throttles", "Sum", "Count"), ("ConcurrentExecutions", "Maximum", "Count")],
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
        dimension = ("InstanceId" if service == "ec2" else "FunctionName")
        dimension_value = (resource["resource_id"] if service == "ec2" else
                           resource["resource_id"].split(":function:", 1)[-1])
        for name, statistic, unit in METRICS[service]:
            response = client.get_metric_statistics(
                Namespace="AWS/EC2" if service == "ec2" else "AWS/Lambda",
                MetricName=name, Dimensions=[{"Name": dimension, "Value": dimension_value}],
                StartTime=start, EndTime=end, Period=300, Statistics=[statistic], Unit=unit)
            by_time = {}
            for point in response.get("Datapoints", []):
                timestamp = point["Timestamp"].astimezone(timezone.utc)
                value = point.get(statistic)
                if start <= timestamp < end and value is not None and math.isfinite(value):
                    by_time[timestamp.isoformat()] = float(value)
            points = [{"timestamp": timestamp, "value": value}
                      for timestamp, value in sorted(by_time.items())]
            latest = points[-1] if points else None
            series.append({"resource_id": resource["resource_id"], "service": service,
                           "metric": name, "statistic": statistic, "unit": unit,
                           "period_seconds": 300, "points": points,
                           "status": "available" if points else "missing",
                           "latest": latest, "expected_points": expected_points,
                           "missing_points": max(0, expected_points - len(points))})
    return {"window_start": start.isoformat(), "window_end": end.isoformat(),
            "collected_resources": len(selected), "skipped_resources": len(eligible) - len(selected),
            "api_calls": len(series), "series": series}
