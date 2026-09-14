# ☁️ AWS Cost Intelligence System

A real-world cloud cost intelligence platform that connects to an AWS account (or local emulator), detects genuine cost anomalies using lightweight statistical analysis, and autonomously executes safe optimizations through AWS APIs — all visualized on a real-time dashboard.

---

## 🏗️ System Architecture

```
app.py (Unified Single-Process Entry Point)
  ├── Flask Web Server (:5000)
  │     ├── Serves dashboard.html (Vanilla JS + Chart.js, zero build tools)
  │     └── REST API (/api/cost-trend, /api/anomalies, /api/optimization-log, /api/savings-summary)
  │
  └── Background Pipeline Worker (every 15 min)
        │
        ├── 1. Collector (collector.py)
        │     ├── Billing Metrics (Cost Explorer / Mock)
        │     ├── Utilization Metrics (CloudWatch CPU)
        │     └── Resource Inventory (EC2, Lambda, S3)
        │     ↓ writes to
        │     CostTelemetry (DynamoDB Table)
        │
        ├── 2. Statistical Anomaly Detector (anomaly_detector.py)
        │     ├── Statistical moving baseline (time-series CPU spikes & idle)
        │     └── Multivariate correlation (cost vs utilization rules)
        │     ↓ writes to
        │     AnomalyEvents (DynamoDB Table)
        │
        └── 3. Optimization Engine (optimization_engine.py)
              ├── stop_ec2_instance (idle instances)
              ├── cap_lambda_concurrency (runaway functions)
              ├── tag_resource_for_review (cost spikes & orphaned volumes)
              └── Circuit Breaker (max 5 actions/hour)
              ↓ writes to
              OptimizationAudit (DynamoDB Table)
```

---

## 📁 Project Structure

```
Cloud_Cost_Intelligence_System/
│
├── .env.example                  ← Environment template with all options documented
├── .gitignore                    ← Git ignore rules (caches, secrets, environments)
├── .dockerignore                 ← Docker build ignore rules
├── Dockerfile                    ← Production container image definition
├── docker-compose.yml            ← Multi-container setup (App + DynamoDB Local)
├── cloudformation.yaml           ← AWS CloudFormation template (DynamoDB tables + IAM)
│
├── requirements.txt              ← Production dependencies (boto3, flask, python-dotenv, pytest)
├── requirements-dev.txt          ← Dev & test dependencies (includes moto for offline mocking)
├── README.md                     ← Project overview and documentation
├── RUNBOOK.md                    ← Full operational and troubleshooting runbook
│
├── aws_clients.py                ← Centralized lazy boto3 initialization with endpoint_url support
├── setup_aws.py                  ← Idempotent table & credential verification script
├── app.py                        ← Unified single-process entry point (API + background pipeline)
│
├── db_utils.py                   ← Shared DynamoDB pagination utility (scan_all)
├── collector.py                  ← Telemetry pipeline (billing, CloudWatch utilization, inventory)
├── generate_training_data.py     ← Synthetic historical data generator with anomaly injection
├── anomaly_detector.py           ← Lightweight statistical & multivariate anomaly detection
├── optimization_engine.py        ← Autonomous optimization engine with circuit breaker
├── rollback.py                   ← Smart audit-based action rollback (with --dry-run)
├── dashboard_api.py              ← Flask REST API (serves dashboard UI + 5 endpoints)
├── dashboard.html                ← Vanilla JS dashboard + Chart.js (single file, zero build tools)
├── run_pipeline.py               ← Pipeline loop runner with graceful shutdown
│
├── test_pipeline.py              ← 17 automated offline tests with moto mocking
└── validate_savings.py           ← Cost attribution and real Cost Explorer validator
```

---

## 🚀 Quick Start

You can run the system either **locally on your machine** or **fully offline in Docker with DynamoDB Local**.

### Option A: Fully Offline with Docker (Fastest — 0 AWS Setup)

No AWS account or credentials needed:

```bash
docker compose up
```

This starts:
1. **DynamoDB Local** emulator on `http://localhost:8000`
2. **Initializer** running `setup_aws.py` to create tables automatically
3. **Cost Intelligence App** running `app.py` at `http://localhost:5000`

Open `http://localhost:5000` in your browser. Done!

---

### Option B: Local Python Setup

#### 1. Clone the repository
```bash
git clone https://github.com/Hardik-Rawat/Cloud_Cost_Intelligence_System.git
cd Cloud_Cost_Intelligence_System
```

#### 2. Create virtual environment
```powershell
# Windows
python -m venv venv
.\venv\Scripts\Activate.ps1

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

#### 3. Install dependencies
```bash
pip install -r requirements.txt
```

#### 4. Configure environment
```bash
cp .env.example .env
```
Edit `.env` with your AWS credentials or leave default settings for local development.

#### 5. Initialize DynamoDB tables
```bash
python setup_aws.py
```
This idempotent script verifies your AWS credentials (or local DynamoDB endpoint) and creates all required tables (`CostTelemetry`, `AnomalyEvents`, `OptimizationAudit`).

#### 6. Start the unified application
```bash
python app.py
```
This launches the Flask web server and the background telemetry pipeline in a single process.
Open **`http://localhost:5000`** in your browser to view the dashboard!

---

## ⚙️ CLI Options & Commands

| Task | Command |
|---|---|
| Start unified app (Dashboard + Pipeline) | `python app.py` |
| Start only dashboard UI & API | `python app.py --api-only` |
| Start only pipeline runner | `python app.py --pipeline-only` |
| Run pipeline once and exit | `python run_pipeline.py --once` |
| Preview rollback actions | `python rollback.py --dry-run` |
| Execute rollback of tracked actions | `python rollback.py` |
| Emergency account-wide rollback | `python rollback.py --all-resources` |
| Validate savings attribution | `python validate_savings.py` |
| Run offline test suite | `pytest test_pipeline.py -v` |

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
- Controlled by `CONFIDENCE_THRESHOLD = 0.85` in `.env`:
| Value | Effect |
|---|---|
| 0.70 | More detections, higher sensitivity |
| 0.85 | Default balanced setting |
| 0.95 | Fewer detections, very high precision only |

Only anomalies meeting or exceeding the threshold are escalated to the `AnomalyEvents` table in DynamoDB for autonomous remediation.

---

## ⚙️ Optimization Actions & Smart Rollback

| Anomaly Type | Action | Reversible? | Rollback Method |
|---|---|---|---|
| `idle_instance` | Stop EC2 instance | ✅ Yes | `python rollback.py` / `aws ec2 start-instances` |
| `cpu_spike` | Cap Lambda concurrency | ✅ Yes | `python rollback.py` / `aws lambda delete-function-concurrency` |
| `runaway_function` | Cap Lambda concurrency | ✅ Yes | `python rollback.py` / `aws lambda delete-function-concurrency` |
| `cost_spike` | Tag resource for review (`review-needed=true`) | ✅ Yes | `python rollback.py` / `aws ec2 delete-tags` |
| `orphaned_volume` | Tag resource for review (`review-needed=true`) | ✅ Yes | `python rollback.py` / `aws ec2 delete-tags` |

**Circuit breaker:** A maximum of 5 actions are executed per engine run (configurable via `MAX_ACTIONS_PER_HOUR`) to prevent runaway automation. Every action records an exact rollback CLI command in `OptimizationAudit`.

**Smart Rollback:** `rollback.py` queries `OptimizationAudit` to undo *only* the specific actions executed by this system, updating audit records to `rolled_back`. Use `--dry-run` to inspect actions before applying them.

---

## 📊 Dashboard Panels & API

The frontend (`dashboard.html`) is built in pure Vanilla JavaScript (ES6+) with Chart.js and modern CSS, requiring zero compilation or bundlers. It is served directly by Flask at `/`:

| Panel / Feature | API Endpoint | Data Source | Refresh Rate |
|---|---|---|---|
| Cost Trend Chart | `/api/cost-trend` | `CostTelemetry` DynamoDB | 60s (auto) |
| Anomaly Feed | `/api/anomalies` | `AnomalyEvents` DynamoDB | 60s (auto) |
| Optimization Log | `/api/optimization-log` | `OptimizationAudit` DynamoDB | 60s (auto) |
| Savings Summary | `/api/savings-summary` | `OptimizationAudit` DynamoDB | 60s (auto) |
| Health Check | `/api/health` | Service status | On demand |

---

## ☁️ Infrastructure as Code (CloudFormation)

A complete AWS CloudFormation template is provided in `cloudformation.yaml` to provision all required DynamoDB tables and an IAM execution role with least-privilege permissions:

```bash
aws cloudformation create-stack \
  --stack-name CloudCostIntelligenceStack \
  --template-body file://cloudformation.yaml \
  --capabilities CAPABILITY_NAMED_IAM
```

---

## 🧪 Automated Testing (100% Offline)

To install testing dependencies and run the complete offline test suite:
```bash
pip install -r requirements-dev.txt
pytest test_pipeline.py -v
```

The test suite uses `moto` to mock all AWS services (DynamoDB, EC2, Lambda, CloudWatch, S3) in-memory:
- **17 test cases across 7 test classes**
- Zero live AWS credentials or cloud costs required
- Dynamic resource generation (no hardcoded instance IDs)
- Full end-to-end integration and rollback verification

---

## 📖 Operational Runbook

See [RUNBOOK.md](./RUNBOOK.md) for detailed instructions on:
- Tuning detection thresholds
- Adding new AWS resource types
- Swapping mock data for real Cost Explorer
- Manual rollback procedures
- Operational troubleshooting

---

## 📄 License

MIT License — free to use, modify, and distribute.