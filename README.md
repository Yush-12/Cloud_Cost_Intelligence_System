# ☁️ AWS Cost Intelligence System

A real-world cloud cost intelligence platform that connects to a live AWS account, detects genuine cost anomalies using statistical analysis, and autonomously executes safe optimizations through AWS APIs — all visualized on a real-time dashboard.

---

## 🏗️ System Architecture

```
Collector (every 15 min)
    ↓ writes to
CostTelemetry (DynamoDB)
    ↓ read by
Anomaly Detector
  ├── Statistical Time-Series Detector (Z-score, moving baseline)
  └── Multivariate Correlation Detector (CPU + billing correlation)
    ↓ writes to
AnomalyEvents (DynamoDB)
    ↓ read by
Optimization Engine
  ├── stop_ec2_instance
  ├── cap_lambda_concurrency
  └── tag_resource_for_review
    ↓ writes to
OptimizationAudit (DynamoDB)
    ↓ read by
Dashboard API (Flask :5000)
    ↓ served to
dashboard.html (Vanilla JS + Chart.js, auto-refreshes 60s)
```

---

## 📁 Project Structure

```
Cloud_Cost_Intelligence_System/
│
├── .env.example                  ← Copy to .env and fill in values
├── .gitignore                    ← Git ignore rules (caches, secrets, environments)
├── requirements.txt              ← Lean dependencies (boto3, flask, python-dotenv, pytest)
├── README.md                     ← Project overview and documentation
├── RUNBOOK.md                    ← Full operational guide
│
├── db_utils.py                   ← Shared DynamoDB pagination utility (scan_all)
│
├── collector.py                  ← Telemetry pipeline (billing, utilization, inventory)
│
├── generate_training_data.py     ← Synthetic historical data generator with anomaly injection
├── anomaly_detector.py           ← Lightweight statistical & multivariate anomaly detection
│
├── optimization_engine.py        ← Autonomous optimization engine with circuit breaker
├── rollback.py                   ← Undo all optimization actions
│
├── dashboard_api.py              ← Flask REST API (5 endpoints with CORS support)
├── dashboard.html                ← Vanilla JS dashboard + Chart.js (single file, zero build tools)
├── run_pipeline.py               ← Pipeline loop runner (direct function imports, every 15 min)
│
├── test_pipeline.py              ← 18 automated tests across 7 test classes (pytest)
└── validate_savings.py           ← Cost attribution validator
```

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- AWS account (free tier works)
- Windows (PowerShell) or Linux/macOS

### 1. Clone the repo
```bash
git clone https://github.com/Hardik-Rawat/Cloud_Cost_Intelligence_System.git
cd Cloud_Cost_Intelligence_System
```

### 2. Create virtual environment
```powershell
# Windows
python -m venv venv
.\venv\Scripts\Activate.ps1

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```
> The dependency footprint is deliberately minimal: only `boto3`, `flask`, `python-dotenv`, and `pytest` are required.

### 4. Configure environment
```bash
cp .env.example .env
# Edit .env with your AWS credentials and resource details
```

### 5. Configure AWS CLI
```bash
aws configure
# Enter your Access Key ID, Secret, region (us-east-1), and output format (json)
```

---

## 📋 Phase-by-Phase Setup

### Phase 1 — Foundation & Cloud Provisioning
Provision AWS resources manually via AWS Console or CLI:
- IAM Role (`CostIntelligenceLambdaRole`) + IAM User (`cost-intelligence-local`)
- EC2 `t2.micro` instance
- S3 bucket
- RDS `db.t2.micro` PostgreSQL
- DynamoDB table (`CostTelemetry`)
- Lambda function (`cost-telemetry-collector`)

### Phase 2 — Telemetry Pipeline
Collect 3 metric streams (billing, CloudWatch utilization, resource inventory) into DynamoDB:
```bash
python collector.py
```

### Phase 3 — Statistical Anomaly Detection
Generate synthetic historical data (optional, for demo/testing) and run anomaly detection:
```bash
python generate_training_data.py
python anomaly_detector.py
```

### Phase 4 — Autonomous Optimization Engine
Execute safe, reversible actions on detected anomalies:
```bash
python optimization_engine.py

# To undo all optimization actions:
python rollback.py
```

### Phase 5 — Real-Time Dashboard
Open two PowerShell/terminal windows:

**Window 1 — API:**
```bash
python dashboard_api.py
```

**Window 2 — Pipeline:**
```bash
python run_pipeline.py
```

Then open `dashboard.html` directly in your browser.

### Phase 6 — Validation & Testing
```bash
# Run full test suite (18 tests)
pytest test_pipeline.py -v

# Validate cost savings attribution
python validate_savings.py
```

---

## 🔍 Anomaly Detection Engine

The anomaly detection engine in `anomaly_detector.py` uses lightweight statistical methods from Python's standard library (`statistics`), providing deterministic, fast detection without heavy ML library overhead.

### 1. Statistical Time-Series Detector
- **Methodology**: Computes moving average and standard deviation over chronological CPU utilization records.
- **CPU Spike Detection**: Flags instances where CPU utilization is $\ge 80\%$ or $\ge 2.5$ standard deviations above baseline. Confidence scales dynamically ($0.85 - 0.99$) based on severity.
- **Idle Instance Detection**: Flags instances where CPU utilization is $\le 1.0\%$ or $\ge 2.5$ standard deviations below baseline. Confidence scales based on idle ratio.
- **Anomaly Types**: `cpu_spike`, `idle_instance`

### 2. Multivariate Correlation Detector
- **Methodology**: Aggregates CPU utilization and billing costs into hourly buckets to identify cross-metric anomalies (with a direct cost fallback for isolated billing records).
- **Runaway Function**: High CPU ($> 70\%$) combined with elevated billing ($> \$0.50$).
- **Orphaned Volume**: Near-zero CPU ($< 2\%$) accompanied by persistent billing ($> \$0.10$).
- **Cost Spike**: Unusually high billing surge ($> \$1.00$).
- **Idle Instance**: Low CPU ($< 1.0\%$) over evaluation windows.
- **Anomaly Types**: `runaway_function`, `orphaned_volume`, `cost_spike`, `idle_instance`

### 3. Confidence Threshold & Escalation
- Controlled by `CONFIDENCE_THRESHOLD = 0.85` in `anomaly_detector.py`:
| Value | Effect |
|---|---|
| 0.70 | More detections, higher sensitivity |
| 0.85 | Default balanced setting |
| 0.95 | Fewer detections, very high precision only |

Only anomalies meeting or exceeding the threshold are escalated to the `AnomalyEvents` table in DynamoDB for autonomous remediation.

---

## ⚙️ Optimization Actions

| Anomaly Type | Action | Reversible? | Rollback Method |
|---|---|---|---|
| `idle_instance` | Stop EC2 instance | ✅ Yes | `aws ec2 start-instances` / `python rollback.py` |
| `cpu_spike` | Cap Lambda concurrency | ✅ Yes | `aws lambda delete-function-concurrency` / `python rollback.py` |
| `runaway_function` | Cap Lambda concurrency | ✅ Yes | `aws lambda delete-function-concurrency` / `python rollback.py` |
| `cost_spike` | Tag resource for review (`review-needed=true`) | ✅ Yes | `aws ec2 delete-tags` / `python rollback.py` |
| `orphaned_volume` | Tag resource for review (`review-needed=true`) | ✅ Yes | `aws ec2 delete-tags` / `python rollback.py` |

**Circuit breaker:** A maximum of 5 actions are executed per engine run to prevent runaway automation. Every action records an exact rollback CLI command in the `OptimizationAudit` table.

---

## 📊 Dashboard Panels & API

The frontend (`dashboard.html`) is built in pure Vanilla JavaScript (ES6+) with Chart.js and modern CSS, requiring zero compilation or bundlers. It fetches data from `dashboard_api.py`:

| Panel / Feature | API Endpoint | Data Source | Refresh Rate |
|---|---|---|---|
| Cost Trend Chart | `/api/cost-trend` | `CostTelemetry` DynamoDB | 60s (auto) |
| Anomaly Feed | `/api/anomalies` | `AnomalyEvents` DynamoDB | 60s (auto) |
| Optimization Log | `/api/optimization-log` | `OptimizationAudit` DynamoDB | 60s (auto) |
| Savings Summary | `/api/savings-summary` | `OptimizationAudit` DynamoDB | 60s (auto) |
| Health Check | `/api/health` | Service status | On demand |

---

## 🛠️ IAM Policy

The IAM user/role requires the following policy (`CostIntelligencePolicy`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ce:GetCostAndUsage",
        "ce:GetCostForecast",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics",
        "ec2:DescribeInstances",
        "ec2:StopInstances",
        "ec2:StartInstances",
        "ec2:CreateTags",
        "ec2:DeleteTags",
        "lambda:ListFunctions",
        "lambda:PutFunctionConcurrency",
        "lambda:DeleteFunctionConcurrency",
        "lambda:GetFunctionConcurrency",
        "lambda:GetAccountSettings",
        "s3:ListAllMyBuckets",
        "rds:DescribeDBInstances",
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "dynamodb:ListTables",
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
        "dynamodb:Query",
        "dynamodb:Scan",
        "dynamodb:DescribeTable",
        "dynamodb:BatchWriteItem",
        "dynamodb:CreateTable"
      ],
      "Resource": "*"
    }
  ]
}
```

---

## 🔄 Swapping Mock → Real Cost Explorer

`collector.py` includes inlined mock billing data via `get_cost_data_mock()` for local testing prior to Cost Explorer activation.

Once AWS Cost Explorer activates in your account (typically 24 hours after enabling in the AWS Console), update `collect_billing_metrics()` in `collector.py`:

Replace:
```python
records = get_cost_data_mock()
```

With:
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
for result in response["ResultsByTime"]:
    for group in result["Groups"]:
        records.append({
            "resource_id": group["Keys"][0],
            "service": group["Keys"][0],
            "region": REGION,
            "cost_usd": float(group["Metrics"]["BlendedCost"]["Amount"]),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
```

---

## 🧪 Test Suite

Run the full automated test suite using `pytest`:
```bash
pytest test_pipeline.py -v
```

| Test Class | Tests | What It Covers |
|---|---|---|
| `TestCollector` | 5 | All 3 metric streams (billing, utilization, inventory), field validation |
| `TestCPUSpikeDetection` | 2 | CPU spike detection, standard deviation thresholds, confidence scaling |
| `TestIdleInstanceDetection` | 2 | Idle instance detection, idle thresholding, confidence calculation |
| `TestCostSpikeDetection` | 1 | Cost spike storage and metadata validation |
| `TestOptimizationEngine` | 4 | Audit records, rollback commands, resource-type tagging, circuit breaker (5-action limit) |
| `TestDashboardAPI` | 3 | Cost trend, anomalies, and optimization log API endpoints |
| `TestEndToEnd` | 1 | Full integration pipeline: telemetry ingest → detection → action → audit |

All 18 tests pass with zero external service dependencies required during mocked runs.

---

## 🔧 Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `AccessDeniedException` | Missing IAM action | Add the missing permission to `CostIntelligencePolicy` in IAM |
| `ResourceNotFoundException` | DynamoDB table does not exist | Verify table names in `.env` match created DynamoDB tables |
| `TypeError: Unsupported type datetime` | Datetime serialization | Call `.isoformat()` on all datetime objects before persisting |
| `InvalidParameterValueException` (Lambda) | Concurrency quota limit | Free-tier limit reached; the engine automatically falls back to resource tagging |
| Dashboard shows stale data | API or runner stopped | Ensure `dashboard_api.py` and `run_pipeline.py` are running; click ⟳ Refresh Data |

---

## 📖 Operational Runbook

See [RUNBOOK.md](./RUNBOOK.md) for:
- How to tune the confidence threshold
- How to add a new AWS resource type
- How to roll back specific actions
- How to swap mock data for real Cost Explorer
- Operational troubleshooting procedures

---

## 🏷️ Tech Stack

| Layer | Technology | Details |
|---|---|---|
| Cloud Platform | AWS | EC2, Lambda, S3, RDS, DynamoDB, CloudWatch, Cost Explorer |
| Cloud SDK | boto3 (Python) | AWS API interactions & resource management |
| Anomaly Detection | Python `statistics` | Standard library moving average, standard deviation, Z-scores (zero ML overhead) |
| Backend & REST API | Flask 3.1 | REST API with native CORS header handling |
| Frontend | Vanilla JavaScript (ES6+), HTML5, CSS3 | Single-file responsive dashboard, Chart.js for data visualization |
| Testing | pytest | Unit, functional, and end-to-end integration tests |
| Configuration | python-dotenv | Environment variable management |

---

## ⚠️ Important Notes

- **Never commit `.env`** — it contains your AWS credentials. It is gitignored.
- The system only uses **free-tier AWS resources** — costs should be $0 or near $0.
- All optimization actions are **safe and reversible** — run `rollback.py` anytime.
- The circuit breaker limits actions to **5 per run** to prevent accidental over-automation.

---

## 📄 License

MIT License — free to use, modify, and distribute.