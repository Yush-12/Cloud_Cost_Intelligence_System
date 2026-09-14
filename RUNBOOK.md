# AWS Cost Intelligence System — Operational Runbook

## Quick Start
| Task | Command |
|---|---|
| Initialize AWS & DynamoDB tables | `python scripts/setup_aws.py` |
| Start unified app (Dashboard UI + Pipeline) | `python app.py` |
| Start dashboard API & UI only | `python app.py --api-only` (or `python web/dashboard_api.py`) |
| Run full pipeline once | `python src/pipeline.py --once` |
| Run pipeline continuously in foreground | `python src/pipeline.py` |
| Run entire stack offline in Docker | `docker compose up` |
| Roll back actions (dry run) | `python src/rollback.py --dry-run` |
| Roll back all tracked actions | `python src/rollback.py` |
| Validate savings attribution | `python scripts/validate_savings.py` |
| Run offline test suite | `pytest tests/ -v` |

---

## Initial Setup & Verification

Before running the system for the first time:

1. **Copy configuration template:**
   ```powershell
   cp .env.example .env
   ```
2. **Configure credentials:**
   Set `AWS_REGION`, `AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY` in `.env`.
   *(Or omit if using `DYNAMODB_ENDPOINT_URL` for local development).*
3. **Run setup script:**
   ```powershell
   python scripts/setup_aws.py
   ```
   This validates your AWS credentials or local endpoint and creates all 3 required DynamoDB tables (`CostTelemetry`, `AnomalyEvents`, `OptimizationAudit`) with `PAY_PER_REQUEST` billing mode.

---

## How to Tune the Confidence Threshold

**File:** `src/anomaly_detector.py` or `.env`
**Variable:** `CONFIDENCE_THRESHOLD = 0.85`

| Value | Effect |
|---|---|
| 0.70 | More anomalies detected, more false positives |
| 0.85 | Default — balanced precision and recall |
| 0.95 | Fewer detections, very high precision only |

Change in `.env` and re-run — no code changes required.

---

## How to Add a New Resource Type

Example: Adding ElastiCache monitoring

1. **Add collector stream** in `src/collector.py`:
```python
elasticache = boto3.client('elasticache', region_name=REGION)
clusters = elasticache.describe_cache_clusters()
for cluster in clusters.get("CacheClusters", []):
    table.put_item(Item={
        "resource_id": cluster["CacheClusterId"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metric_type": "inventory",
        "service": "ElastiCache",
        "region": REGION,
        "state": cluster["CacheClusterStatus"],
        "instance_type": cluster["CacheNodeType"]
    })
```
2. **Add IAM permission:** `elasticache:DescribeCacheClusters`
3. **Add anomaly rule** in `src/optimization_engine.py` `ACTION_RULES` dictionary.
4. **Add action function** following the same pattern as `stop_ec2_instance`.

---

## How to Roll Back Optimization Actions

Every action executed by `src/optimization_engine.py` is logged in `OptimizationAudit` with an audit record and rollback command.

**Option A — Safe rollback of tracked actions (Recommended):**
```powershell
# Preview what would be rolled back
python src/rollback.py --dry-run

# Execute rollback for all tracked actions
python src/rollback.py
```
This restarts only the EC2 instances stopped by this system, removes concurrency caps from capped Lambdas, and clears review tags, then marks the audit records as `rolled_back`.

**Option B — Emergency account-wide rollback:**
```powershell
python src/rollback.py --all-resources
```

**Option C — Roll back a specific action manually via AWS CLI:**
```powershell
# Restart a specific EC2 instance
aws ec2 start-instances --instance-ids i-0aa9b48a77f3f6bd7

# Remove Lambda concurrency cap
aws lambda delete-function-concurrency --function-name cost-telemetry-collector

# Remove review tags
aws ec2 delete-tags --resources i-0aa9b48a77f3f6bd7 --tags Key=review-needed
```

---

## How to Swap Mock Cost Data for Real Cost Explorer

In `src/collector.py`, replace the `get_cost_data_mock()` call inside `collect_billing_metrics()` with real AWS Cost Explorer:

```python
ce = boto3.client('ce', region_name='us-east-1')
response = ce.get_cost_and_usage(
    TimePeriod={
        "Start": (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d"),
        "End": datetime.now(timezone.utc).strftime("%Y-%m-%d")
    },
    Granularity="DAILY",
    Metrics=["BlendedCost"],
    GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}]
)
records = []
for result in response.get("ResultsByTime", []):
    for group in result.get("Groups", []):
        records.append({
            "resource_id": group["Keys"][0],
            "service": group["Keys"][0],
            "region": REGION,
            "cost_usd": float(group["Metrics"]["BlendedCost"]["Amount"]),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
```

---

## Troubleshooting Common Errors

| Error | Cause & Fix |
|---|---|
| `AccessDeniedException` | Add the missing action to `CloudCostIntelligenceExecutionRole` in IAM or `infra/cloudformation.yaml` |
| `ResourceNotFoundException` | DynamoDB table does not exist. Run `python scripts/setup_aws.py` to initialize tables |
| `NoCredentialsError` | AWS credentials missing. Set `AWS_ACCESS_KEY_ID` in `.env` or run `aws configure` |
| `TypeError: Unsupported type datetime` | Call `.isoformat()` on all datetime objects before storing in DynamoDB |
| `Cannot compare tz-naive and tz-aware` | Ensure all datetimes use `datetime.now(timezone.utc)` |
| `EndpointConnectionError` | DynamoDB Local is not running. Start it with `docker compose up dynamodb-local` |
| Insufficient data for anomaly detection | Run `python scripts/generate_training_data.py` to populate baseline metrics |
| Dashboard shows stale data | Click ⟳ Refresh or verify pipeline worker in `python app.py` |

---

## System Architecture Summary

```
app.py (Unified Single-Process Entry Point)
  ├── Flask Web Server (:5000) [web/dashboard_api.py]
  │     ├── Serves dashboard.html (Vanilla JS + Chart.js) [web/dashboard.html]
  │     └── REST API (/api/cost-trend, /api/anomalies, /api/optimization-log, /api/savings-summary)
  │
  └── Background Pipeline Worker (every 15 min) [src/pipeline.py]
        │
        ├── 1. Collector (src/collector.py)
        │     ├── Billing Metrics (Cost Explorer / Mock)
        │     ├── Utilization Metrics (CloudWatch CPU)
        │     └── Resource Inventory (EC2, Lambda, S3)
        │     ↓ writes to
        │     CostTelemetry (DynamoDB Table)
        │
        ├── 2. Statistical Anomaly Detector (src/anomaly_detector.py)
        │     ├── Statistical moving baseline (time-series CPU spikes & idle)
        │     └── Multivariate correlation (cost vs utilization rules)
        │     ↓ writes to
        │     AnomalyEvents (DynamoDB Table)
        │
        └── 3. Optimization Engine (src/optimization_engine.py)
              ├── stop_ec2_instance (idle instances)
              ├── cap_lambda_concurrency (runaway functions)
              ├── tag_resource_for_review (cost spikes & orphaned volumes)
              └── Circuit Breaker (max 5 actions/hour)
              ↓ writes to
              OptimizationAudit (DynamoDB Table)
```
