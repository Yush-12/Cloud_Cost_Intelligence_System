# Cloud Cost Intelligence System

> **A real cloud cost intelligence platform connected to actual AWS infrastructure that monitors live resource usage, detects genuine cost anomalies using Machine Learning (Isolation Forest), and safely executes optimizations via cloud APIs.**

---

## 🌟 Executive Overview & Core Capabilities

The `costintel` package is the production-grade implementation connected to live AWS infrastructure (tested in Mumbai `ap-south-1`, under AWS Free Tier / Tutorial credits).

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
**Result**: `61 passed in ~30s`.

---

## 📖 Documentation & Runbook

- **Operational Runbook**: See [`RUNBOOK.md`](RUNBOOK.md) for full incident response, IAM policy templates, threshold tuning, and emergency rollback procedures.
- **Experiment Evidence & Lab Journal**: See [`docs/CHECKPOINT.md`](docs/CHECKPOINT.md) for detailed development history, lab evidence, and experiment checkpoint logs.

---

---

## 📄 License

MIT License — free to use, modify, and distribute.
