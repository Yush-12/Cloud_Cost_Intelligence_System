# Cloud Cost Intelligence System

> **A real cloud cost intelligence platform connected to actual AWS infrastructure that monitors live resource usage, detects genuine cost anomalies using Machine Learning (Isolation Forest), and safely executes optimizations via cloud APIs.**

---

## 🌟 Executive Overview & Core Capabilities

The `costintel` package is the production-grade implementation connected to live AWS infrastructure (tested in Mumbai `ap-south-1`, AWS account `695313591289` under AWS Free Tier / Tutorial credits).

### Key Features:
- **Real Resource Inventory & Telemetry**: Paginated AWS SDK reads for EC2 instances and Lambda functions, with bounded CloudWatch 5-minute metric streams (`CPUUtilization`, `NetworkIn`, `NetworkOut`, `Invocations`, `Errors`, `Duration`, `Throttles`, `ConcurrentExecutions`).
- **Missing Telemetry Semantics**: Never fabricates zeroes for absent CloudWatch datapoints; absent intervals remain explicitly marked as missing.
- **ML Anomaly Detection (Isolation Forest)**: Trains on 36 consecutive 5-minute baseline buckets (3 hours) and evaluates held-out samples. Detects slow function execution spikes, runaway concurrency, and idle compute patterns.
- **Four-Category Safe Cloud Optimization Engine**:
  1. **Limiting Function Concurrency**: Reversibly throttles runaway Lambda functions to concurrency `0` via `lambda:PutFunctionConcurrency`, with complete restoration to unreserved concurrency via `lambda:DeleteFunctionConcurrency`.
  2. **Tagging Unreviewed Assets**: Commits idempotent review tags (`CostIntelReview: review-<sha256>`) to anomalous resources for human-in-the-loop audit.
  3. **Stopping Idle Compute Resources**: Safely stops running EC2 instances with low CPU utilization (average `< 2.0%`) via `ec2:StopInstances`, with full restart recovery via `ec2:StartInstances`.
  4. **Cleaning Unused Volumes**: Identifies unattached EBS storage volumes (state: `available`) and tags them for scheduled reclamation.
- **Strict Guardrails & Accountability**:
  - Rejects root credentials (`sts:GetCallerIdentity` enforcement).
  - Explicit multi-tag opt-in required (`CostIntelLab=true` and `AutoOptimize=true` or `CostIntelContainment=true`).
  - Durable SQLite audit trails committed *before* API mutations; uncertain states are recorded and never silently replayed.
  - Safe dry-run default across all commands.
- **Interactive Web Dashboard**:
  - Accessible via `http://127.0.0.1:5002/`.
  - Dark and Light modes with zero flash-of-light-theme.
  - Gap-aware SVG charts with accessible fallback tables.
  - Multi-service accountability panels for containment audits, review tag audits, and compute/storage remediation audits.

---

## 🏗️ Rebuilt System Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │               AWS ap-south-1                 │
                    │   EC2 Instances · Lambda Labs · CloudWatch   │
                    └──────────────┬───────────────────▲───────────┘
                                   │                   │
                  Read-Only Reads  │                   │ Safe API Optimizations
           (ec2:DescribeInstances, │                   │ (PutFunctionConcurrency,
            lambda:ListFunctions,  │                   │  StopInstances,
            GetMetricStatistics)   │                   │  TagResource)
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │      costintel Collector     │   │
                    │   (inventory.py & telemetry) │   │
                    └──────────────┬───────────────┘   │
                                   │                   │
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │    SQLite Time-Series Store  │   │
                    │  (.local-data/inventory.db)  │   │
                    │  Snapshots, Observations,    │   │
                    │  Audits (Review/Containment) │   │
                    └──────────────┬───────────────┘   │
                                   │                   │
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │  Isolation Forest Detector   │   │
                    │        (detection.py)        │   │
                    │   36-bucket baseline tail    │   │
                    └──────────────┬───────────────┘   │
                                   │                   │
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │  Optimization Policy Engine  │───┘
                    │   (actions.py & containment) │
                    │   - Dry-Run by default       │
                    │   - Opt-in tags verification │
                    │   - Rollback / Restoration   │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │    costintel Web Dashboard   │
                    │        (web.py :5002)        │
                    │  Dark/Light Theme · Metrics  │
                    │  Visual Gaps · Action Audits │
                    └──────────────────────────────┘
```

---

## 🚀 Quick Start & How to Run

### 1. Prerequisites (Python 3.12)

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
```

### 2. Run the End-to-End FinOps Pipeline (Demo Mode - Zero AWS Credentials)

```powershell
.venv/Scripts/python -m costintel pipeline --mode demo
```

To view the dashboard with demo data:
```powershell
.venv/Scripts/python -m costintel serve --mode demo --port 5002
```
Open **`http://127.0.0.1:5002`** in your browser.

---

### 3. Run with Real AWS Account

#### Step A: Configure Temporary Non-Root AWS Profile
Create a dedicated IAM user `costintel-reader` with console login and attached `infra/inventory-read-policy.json`.
Then login interactively:
```powershell
aws login --profile costintel-login --region ap-south-1
aws configure set region ap-south-1 --profile costintel
aws configure set credential_process 'aws configure export-credentials --profile costintel-login --format process' --profile costintel
```

#### Step B: Run the Full FinOps Pipeline
```powershell
.venv/Scripts/python -m costintel pipeline --mode aws --profile costintel --account YOUR_ACCOUNT_ID --with-metrics
```
Output summarizes:
- Discovered EC2 & Lambda inventory.
- Ingested CloudWatch metric series.
- Isolation Forest anomaly flags (with exact outlier decision scores).
- Policy optimization recommendations with dry-run protection.

#### Step C: Serve the Live Web Dashboard
```powershell
.venv/Scripts/python -m costintel serve --mode aws --account YOUR_ACCOUNT_ID --port 5002
```
Navigate to **`http://127.0.0.1:5002`** to explore live metrics, theme toggling, and recorded actions.

---

## 🔬 Verified Live AWS Experiment Results

The system has been exercised and verified against real AWS resources:

1. **111-Invocation Controlled Experiment (`scripts/baseline_experiment.py`)**:
   - 36 baseline batches (3 invocations each at 5-minute intervals) + 1 slow batch (3 invocations with 500ms injected delay).
   - All 37 buckets were retrieved from CloudWatch.
   - Isolation Forest flagged the slow bucket (`Duration` 501.77ms vs ~52ms baseline, score `-0.044124`).
   - Idempotent review tag applied to Lambda in AWS, verified, and opt-in tag restored to false.
2. **Live Reversible Containment Verification (`scripts/verify_containment.py`)**:
   - Probe 1 (Before): Succeeded (`200 OK`).
   - Concurrency Throttling: Reserved concurrency set to `0` via `lambda:PutFunctionConcurrency`.
   - Probe 2 (During): Throttled as expected (`429 TooManyRequestsException`).
   - Restoration: Concurrency restored to unreserved state (`lambda:DeleteFunctionConcurrency`).
   - Probe 3 (After): Succeeded (`200 OK`).
   - Journal recorded in `.local-data/experiments/containment.jsonl`.

---

## 🧪 Automated Test Suite

All 74 unit and integration tests run offline without network or credential requirements:

```powershell
.venv/Scripts/python -m pytest tests -q --basetemp=".local-data/pytest-tmp"
```
**Result**: `74 passed in ~40s`.

---

## 📖 Documentation & Runbook

- **Operational Runbook**: See [`RUNBOOK.md`](RUNBOOK.md) for full incident response, IAM policy templates, threshold tuning, and emergency rollback procedures.
- **Experiment Evidence & Lab Journal**: See [`docs/CHECKPOINT.md`](docs/CHECKPOINT.md) for detailed development history, lab evidence, and experiment checkpoint logs.

---

## Legacy simulation documentation

An autonomous cloud cost optimization and FinOps intelligence platform that simulates real-world AWS infrastructure telemetry, detects genuine cost anomalies using statistical models, and executes autonomous remediation actions — with an interactive real-time dashboard and full audit rollback.

Zero AWS credentials, zero cloud costs, and 100% realistic synthetic telemetry.

---

## 🏗️ System Architecture

```
app.py (Unified Single-Process Entry Point)
  ├── Flask Web Server (:5000) [web/dashboard_api.py]
  │     ├── Serves dashboard.html (Vanilla JS + Chart.js, Dark/Light theme)
  │     └── REST API (/api/cost-trend, /api/anomalies, /api/optimization-log, /api/savings-summary)
  │
  └── Background Pipeline Worker (every 15 min) [src/pipeline.py]
        │
        ├── 1. Collector (src/collector.py)
        │     ├── Billing Metrics Stream (Simulated Multi-Service Cost Stream)
        │     ├── Utilization Metrics (Simulated CloudWatch CPU Metrics)
        │     └── Resource Inventory (Simulated EC2, Lambda, S3)
        │     ↓ writes to
        │     CostTelemetry (In-Memory Thread-Safe Table)
        │
        ├── 2. Statistical Anomaly Detector (src/anomaly_detector.py)
        │     ├── Statistical moving baseline (time-series CPU spikes & idle)
        │     └── Multivariate correlation (cost vs utilization rules)
        │     ↓ writes to
        │     AnomalyEvents (In-Memory Table)
        │
        └── 3. Optimization Engine (src/optimization_engine.py)
              ├── stop_ec2_instance (idle instances)
              ├── cap_lambda_concurrency (runaway functions)
              ├── tag_resource_for_review (cost spikes & orphaned volumes)
              └── Circuit Breaker (max 5 actions/hour)
              ↓ writes to
              OptimizationAudit (In-Memory Table)
```

---

## 📁 Project Structure

```
Cloud_Cost_Intelligence_System/
│
├── src/                          # Core Intelligence & Simulation Engine
│   ├── __init__.py
│   ├── aws_clients.py            # Simulated AWS client provider
│   ├── data_store.py             # Thread-safe in-memory DynamoDB & AWS client simulators
│   ├── synthetic_data.py         # Realistic baseline telemetry generator
│   ├── db_utils.py               # Shared pagination & scan utilities
│   ├── collector.py              # Telemetry stream collection
│   ├── anomaly_detector.py       # Statistical & multivariate detection
│   ├── optimization_engine.py    # Remediation rules & circuit breaker
│   ├── rollback.py               # Smart audit-based action rollback
│   └── pipeline.py               # Sequential pipeline orchestrator
│
├── web/                          # Dashboard & REST API
│   ├── __init__.py
│   ├── dashboard_api.py          # Flask REST API & static server
│   └── dashboard.html            # Responsive UI with Dark/Light theme & Chart.js
│
├── scripts/                      # CLI Utilities
│   ├── __init__.py
│   └── generate_training_data.py # Synthetic historical data generator
│
├── tests/                        # Test Suite
│   ├── __init__.py
│   └── test_simulation_mode.py   # Complete offline test suite (13 tests)
│
├── api/                          # Vercel Serverless Function Entry Point
│   └── index.py                  # WSGI handler for Vercel Python runtime
│
├── .github/workflows/            # GitHub Actions Automation
│   └── pipeline_cron.yml         # Scheduled pipeline runner
│
├── app.py                        # Unified single-process runner (Root)
├── vercel.json                   # Vercel deployment & routing configuration
├── requirements.txt              # Production dependencies
├── requirements-dev.txt          # Test dependencies
├── .env.example                  # Environment configuration template
├── README.md                     # Documentation
└── RUNBOOK.md                    # Operational runbook
```

---

## 🚀 Quick Start

### 1. Run Locally (One Command)

```bash
# Clone the repository
git clone https://github.com/Hardik-Rawat/Cloud_Cost_Intelligence_System.git
cd Cloud_Cost_Intelligence_System

# Install dependencies
pip install -r requirements.txt

# Start dashboard UI and background pipeline
python app.py
```

Open **`http://localhost:5000`** in your browser!

### 2. Deploy Live to Vercel (100% Free)

The project is built to deploy seamlessly on Vercel:
1. Push this repository to GitHub.
2. Import the repository into [Vercel](https://vercel.com).
3. Select standard Python/Flask preset or default settings.
4. Deploy! The dashboard is live immediately at `https://your-project.vercel.app`.

---

## ⚙️ CLI Options & Commands

| Task | Command |
|---|---|
| Start unified app (Dashboard + Pipeline) | `python app.py` |
| Start only dashboard UI & API | `python app.py --api-only` |
| Start only pipeline runner | `python app.py --pipeline-only` |
| Run single pipeline cycle and exit | `python src/pipeline.py --once` |
| Preview rollback actions | `python src/rollback.py --dry-run` |
| Execute rollback of tracked actions | `python src/rollback.py` |
| Emergency full rollback | `python src/rollback.py --all-resources` |
| Run test suite | `pytest tests/ -v` |

---

## 🔍 Anomaly Detection Engine

The anomaly detection engine in `src/anomaly_detector.py` uses lightweight statistical methods from Python's standard library (`statistics`), providing deterministic, fast detection without heavy ML overhead:

### 1. Statistical Time-Series Detector
- **Methodology**: Computes moving average and standard deviation over chronological CPU utilization records.
- **CPU Spike Detection**: Flags instances where CPU utilization is $\ge 80\%$ or $\ge 2.5$ standard deviations above baseline. Confidence scales dynamically ($0.85 - 0.99$).
- **Idle Instance Detection**: Flags instances where CPU utilization is $\le 1.0\%$ or $\ge 2.5$ standard deviations below baseline. Confidence scales based on idle ratio.
- **Anomaly Types**: `cpu_spike`, `idle_instance`

### 2. Multivariate Correlation Detector
- **Methodology**: Correlates CPU utilization and billing costs into hourly buckets to catch complex cross-metric anomalies.
- **Runaway Function**: High CPU ($> 70\%$) combined with elevated billing ($> \$0.50$).
- **Orphaned Volume**: Near-zero CPU ($< 2\%$) accompanied by persistent billing ($> \$0.10$).
- **Cost Spike**: Unusually high billing surge ($> \$1.00$).
- **Idle Instance**: Low CPU ($< 1.0\%$) over evaluation windows.
- **Anomaly Types**: `runaway_function`, `orphaned_volume`, `cost_spike`, `idle_instance`

### 3. Confidence Threshold & Escalation
Controlled by `CONFIDENCE_THRESHOLD = 0.85` in `.env`. Only anomalies meeting or exceeding the threshold are escalated for autonomous remediation.

---

## ⚙️ Optimization Actions & Smart Rollback

| Anomaly Type | Action | Reversible? | Rollback Method |
|---|---|---|---|
| `idle_instance` | Stop EC2 instance | ✅ Yes | `python src/rollback.py` |
| `cpu_spike` | Cap Lambda concurrency | ✅ Yes | `python src/rollback.py` |
| `runaway_function` | Cap Lambda concurrency | ✅ Yes | `python src/rollback.py` |
| `cost_spike` | Tag resource for review (`review-needed=true`) | ✅ Yes | `python src/rollback.py` |
| `orphaned_volume` | Tag resource for review (`review-needed=true`) | ✅ Yes | `python src/rollback.py` |

- **Circuit Breaker**: A maximum of 5 actions are executed per engine run (`MAX_ACTIONS_PER_HOUR`) to prevent runaway automation.
- **Smart Rollback**: `src/rollback.py` queries `OptimizationAudit` to undo *only* the specific actions executed by this system, updating audit records to `rolled_back`. Use `--dry-run` to preview actions before applying.

---

## 📊 Dashboard & API

The dashboard UI (`web/dashboard.html`) is built with Vanilla JavaScript (ES6+), modern CSS with Dark/Light theme toggle, and Chart.js.

| Panel / Feature | API Endpoint | Description |
|---|---|---|
| Cost Trend Chart | `/api/cost-trend` | Multi-service time-series billing data |
| Anomaly Feed | `/api/anomalies` | Detected cost & utilization anomalies |
| Optimization Log | `/api/optimization-log` | Autonomous actions executed with rollback commands |
| Savings Summary | `/api/savings-summary` | Aggregated dollar savings & metrics |
| Trigger Cycle | `/api/run-pipeline` | Executes pipeline cycle on-demand |
| Health Check | `/api/health` | Service status & engine health |

---

## 🧪 Automated Testing

Run the offline test suite:
```bash
pytest tests/ -v
```

All 13 tests execute against the thread-safe in-memory store with zero external dependencies.

---

## 📄 License

MIT License — free to use, modify, and distribute.
