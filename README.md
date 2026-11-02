# CostIntel · Autonomous Cloud Cost Intelligence & FinOps Engine

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![AWS SDK](https://img.shields.io/badge/AWS-boto3%20%7C%20Free%20Tier-orange.svg)](https://aws.amazon.com/)
[![Machine Learning](https://img.shields.io/badge/ML-Isolation%20Forest%20(scikit--learn)-green.svg)](https://scikit-learn.org/)
[![Tests](https://img.shields.io/badge/tests-75%20passed%20(100%25)-brightgreen.svg)](tests/)
[![Security](https://img.shields.io/badge/Security-Zero--Persistence%20RAM--Only-blueviolet.svg)](#-zero-persistence-security-model)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 🌟 Executive Overview

**CostIntel** is a production-grade Cloud Cost Intelligence & Autonomous FinOps platform connected to live AWS infrastructure (tested in Mumbai `ap-south-1`, operating strictly within AWS Free Tier bounds).

Unlike static billing dashboards that report costs 24 hours after budget burn has already occurred, CostIntel operates at the real-time telemetry and resource layer:
1. **Multi-Service Telemetry Ingestion**: Continuous 5-minute metric streams across **EC2 Compute**, **Lambda Serverless**, **DynamoDB Database**, and daily **S3 Storage** telemetry.
2. **Unsupervised ML Anomaly Detection**: Gap-aware **Isolation Forest** scoring that distinguishes true anomalous spikes (e.g., 501ms duration vs. 8ms baseline) and prolonged idle compute states from baseline variation.
3. **Policy-Guarded Cloud API Remediation**: Autonomous execution of cloud cost optimizations (concurrency throttling, idle compute shutdown, unattached EBS volume tagging) with strict multi-tag opt-ins and non-root IAM enforcement.
4. **Verified Instant Rollback**: Durable pre-action intent logging in SQLite enabling instant 1-click restoration of compute and concurrency states.
5. **Operator Web Console**: Real-time time-series telemetry charts, Before vs. After optimization impact summaries, 1-click rollbacks, and multi-service resource filtering.

---

## 🚀 Key Innovations & Capabilities

### 1. Zero-Persistence Ephemeral Authentication (`/login`)
* **Enterprise Gateway**: Enter temporary AWS Access Keys or select local IAM profiles (`costintel`, `costintel-deploy`, or `Offline Demo Sandbox`).
* **RAM-Only Lifecycle**: Credentials exist strictly in Python process memory (`active_sessions[sid]`) for the duration of the browser session. **Keys are never written to disk, `.env`, SQLite, or logs.**
* **Non-Root Enforcement**: Validates credentials via `sts:GetCallerIdentity` on connect; root credentials are automatically rejected.
* **1-Click Disconnect**: Instantly purges active credentials from RAM and redirects to the gateway.

### 2. Multi-Service Resource Discovery & Cost Breakdown
Discovers and monitors resources across 5 key AWS services in the selected region:
* **Compute (EC2)**: Live state tracking (`running`, `stopped`), instance types (`t2.micro`), and hourly-to-monthly cost translation (`$8.47/mo`).
* **Serverless (Lambda)**: Runtime configuration, memory allocation (128MB), and invocation cost modeling.
* **Storage Volumes (EBS)**: Paginated discovery of all volumes via `ec2:DescribeVolumes`, attachment state monitoring (`available` vs. `in-use`), and gp3 run-rate annotation (`$0.08/GB-month`).
* **Object Storage (S3)**: Bucket inventory with region filtering via `s3:GetBucketLocation` and daily storage metrics (`BucketSizeBytes` at 86,400s intervals).
* **NoSQL Database (DynamoDB)**: Active tables with billing mode tracking (`PAY_PER_REQUEST` / `PROVISIONED`) and consumed capacity telemetry (`ConsumedReadCapacityUnits`, `ConsumedWriteCapacityUnits`).

### 3. ML Anomaly Detection (Isolation Forest)
* **Gap-Aware Telemetry**: Never treats missing CloudWatch observation intervals as synthetic zeroes; absent data remains explicitly distinct from zero consumption.
* **Continuous Baseline Tail**: Learns dynamic workload bounds from 36 consecutive 5-minute observation buckets (3-hour rolling window).
* **Deterministic Outlier Scoring**: Uses Scikit-Learn's `IsolationForest` to assign decision scores to held-out evaluation points, flagging duration runaway spikes and idle compute anomalies.

### 4. Guarded Autonomous Cloud API Optimization Engine
Four policy-driven remediation categories with full reversibility:
1. **Lambda Runaway Throttling**: Limits function concurrency to `0` via `lambda:PutFunctionConcurrency`, halting cost burn while returning HTTP 429 to callers. Reversible via `lambda:DeleteFunctionConcurrency`.
2. **Stopping Idle Compute**: Automatically halts instances with average CPU $< 2.0\%$ via `ec2:StopInstances` (saving $8.47/month), requiring $\ge 3$ consecutive valid observations to prevent fluke stops. Reversible via `ec2:StartInstances`.
3. **Idempotent Review Tagging**: Applies immutable audit tags (`CostIntelReview: review-<sha256>`) for human-in-the-loop review.
4. **EBS Volume Optimization**: Identifies unattached EBS storage volumes (state: `available`) and flags them for snapshotting or reclamation.

### 5. Automated Live AWS Lab Provisioner & Safe Teardown
* **1-Click Lab Provisioning (`[ 🚀 Provision Lab Resources ]`)**: Automatically creates or verifies tagged bounded test assets (`costintel-lab-workload`, `costintel-lab-idle-ec2`, S3 cache bucket, DynamoDB table).
* **Live Anomaly Stimulation (`[ ⚡ Inject Anomaly Spike ]`)**: Injects controlled 500ms duration spikes into the live Lambda workload to demonstrate real-time ML detection.
* **1-Click Safe Teardown (`[ 🗑️ Teardown Lab ]`)**: Stops compute instances, cleans up tagged test buckets and tables, and resets concurrency to baseline so **zero unexpected AWS charges accrue**.

---

## 🖥️ FinOps Operations Console

CostIntel provides an operator-first web console for telemetry exploration and cloud remediation:

* **Interactive Time-Series Telemetry**: Dynamic SVG metric scrubbers with evaluated outlier points, rolling history windows (`All`, `6h`, `24h`, `48h`), and 1-click CSV export for audit records.
* **Multi-Service Resource Explorer**: Paginated resource inventory with live state indicators, instant substring search, and service-level filtering (EC2, Lambda, EBS, S3, DynamoDB).
* **Before vs. After Optimization Impact**: Visual ledger comparing baseline workload spend against post-optimization run-rates, with quantified monthly dollar impact.
* **Action History & Rollback Controls**: Comprehensive audit log of all containment, compute, and review operations with 1-click instant restoration buttons.
* **Workspace Navigation & Environment Controls**: Retractable sidebar (`Ctrl+B` or toggle button) for high-density fullscreen monitoring, alongside instant light and dark theme switching.

---

## 🏗️ System Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │               AWS ap-south-1                 │
                    │   EC2 Instances · Lambda Labs · CloudWatch   │
                    │         S3 Buckets · DynamoDB Tables         │
                    └──────────────┬───────────────────▲───────────┘
                                   │                   │
                  Live Telemetry   │                   │ Guarded Cloud APIs
             (ec2:DescribeInstances│                   │ (PutFunctionConcurrency,
              lambda:ListFunctions │                   │  StopInstances,
              ec2:DescribeVolumes  │                   │  TagResource,
              s3:ListBuckets       │                   │  DeleteFunctionConcurrency)
              dynamodb:ListTables  │                   │
              GetMetricStatistics) │                   │
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │     costintel Collector      │   │
                    │  (inventory.py & telemetry)  │   │
                    └──────────────┬───────────────┘   │
                                   │                   │
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │   Scoped SQLite Store & RAM  │   │
                    │  Snapshots, Observations,    │   │
                    │  Audit Journal & Lab State   │   │
                    └──────────────┬───────────────┘   │
                                   │                   │
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │   Isolation Forest (ML)      │   │
                    │       (detection.py)         │   │
                    │   36-bucket baseline tail    │   │
                    └──────────────┬───────────────┘   │
                                   │                   │
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │  Optimization Policy Engine  │───┘
                    │   (actions.py & containment) │
                    │   - Dry-Run preview default  │
                    │   - Multi-tag opt-in checks  │
                    │   - 1-Click Rollback/Restore │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │     CostIntel Web Portal     │
                    │      (web.py : port 5002)    │
                    │  - /login (Zero-Persistence) │
                    │  - Collapsible Sidebar Ctrl+B│
                    │  - Live Telemetry Scrubber   │
                    │  - Before vs After Lifecycle │
                    │  - Lab Control Action Bar    │
                    └──────────────────────────────┘
```

---

## 💻 Tech Stack & Standards

| Layer | Technologies & Standards |
|---|---|
| **Backend & Orchestrator** | Python 3.12, Flask, Boto3 (AWS SDK), Botocore |
| **Machine Learning** | Scikit-Learn (`IsolationForest`), NumPy |
| **Data & Telemetry Store** | SQLite3 (durable scoped snapshots, zero external telemetry leaks) |
| **Frontend Console** | Vanilla JavaScript, HTML5, Responsive CSS3 (Zero external UI dependencies) |
| **Cloud Target** | AWS Free Tier (`ap-south-1` Mumbai): EC2, Lambda, EBS, S3, DynamoDB, CloudWatch, STS |
| **Verification & Testing** | Pytest, Boto3 Stubber, Unittest Mock (75 offline unit & integration tests) |

---

## 🏁 Quick Start & Local Execution

### 1. Prerequisites (Python 3.12)

Clone the repository and install dependencies in a virtual environment:

```powershell
git clone https://github.com/YOUR_USERNAME/Cloud_Cost_Intelligence_System.git
cd Cloud_Cost_Intelligence_System

python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
```

---

### 2. Launch the Web Platform

Start the local server on port `5002`:

```powershell
.venv/Scripts/python -m costintel serve --mode aws --account <account_number> --port 5002
```

Open your browser to:
* **Access Gateway**: [`http://127.0.0.1:5002/login`](http://127.0.0.1:5002/login)
* **Dashboard**: [`http://127.0.0.1:5002/`](http://127.0.0.1:5002/)

---

### 3. Using the Web Interface

1. **Sign In**:
   * Enter your temporary AWS IAM credentials (`Access Key`, `Secret Key`, `Region`), OR
   * Connect via local AWS profile (`costintel`, `costintel-deploy`), OR
   * Click **`Offline Demo Sandbox`** for fully simulated offline exploration.
2. **Inspect Multi-Service Resources & Costs**:
   * Review discovered assets across EC2, Lambda, EBS, S3, and DynamoDB.
   * View estimated monthly run-rate breakdown and savings potential per asset.
   * Search or filter resources by service category.
3. **Run the Lab & Remediation**:
   * Click **`[ Collect & preview ]`** to open the workspace operation modal.
   * Choose workload stimulation (`Slow Scenario` or `Baseline Traffic`) and action mode (`Dry Run` or `Autonomous Remediation`).
   * Review live pipeline logs and anomaly decision scores.
   * View the **Before vs After Optimization Impact** panel showing run-rate impact.
   * Click **`[ Restore Baseline ]`** or **`[ Restart Instance ]`** to immediately roll back any optimization.
4. **Sign Out**:
   * Click **`[ ↪ Disconnect ]`** in the sidebar to purge all credentials from RAM.

---

### 4. Running via the Command Line (Headless / CI Mode)

#### A. Run in Simulated Offline Demo Mode (Zero AWS Credentials)
```powershell
.venv/Scripts/python -m costintel pipeline --mode demo
```

#### B. Run Against Real AWS Account (Dry-Run Preview)
```powershell
.venv/Scripts/python -m costintel pipeline --mode aws --profile costintel --account <account_number> --with-metrics
```

#### C. Run and Apply Live Cloud Optimizations
```powershell
.venv/Scripts/python -m costintel pipeline --mode aws --profile costintel-deploy --account <account_number> --with-metrics --apply
```

---

## 🔬 Verified Live AWS Experiment Results

The platform has been rigorously tested against real AWS resources in Mumbai (`ap-south-1`):

1. **Controlled 111-Invocation Experiment (`scripts/baseline_experiment.py`)**:
   * 36 baseline batches (3 invocations each at 5-minute intervals) + 1 slow batch (3 invocations with 500ms delay).
   * All 37 buckets retrieved from CloudWatch without data loss.
   * Isolation Forest flagged the slow bucket: `Duration` 501.77ms vs ~52ms baseline, outlier decision score `-0.044124`.
2. **Live Reversible Containment Verification (`scripts/verify_containment.py`)**:
   * **Stage 1 (Before)**: Invocations succeeded with `200 OK`.
   * **Stage 2 (During)**: Concurrency throttled to `0` via `PutFunctionConcurrency`. Callers safely received `429 TooManyRequestsException`.
   * **Stage 3 (After)**: Unreserved concurrency restored via `DeleteFunctionConcurrency`. Invocations immediately returned `200 OK`.
   * Durable audit record committed to SQLite before and after API invocation.

---

## 🧪 Automated Test Suite

All 75 unit and integration tests run completely offline:

```powershell
.venv/Scripts/python -m pytest tests -q --basetemp=".local-data/pytest-tmp"
```

```text
........................................................................... [100%]
75 passed in 81.35s
```

### Test Coverage Highlights:
* `tests/test_foundation.py`: Snapshot persistence, scope isolation, viewer states, pagination stubbing, root credential rejection.
* `tests/test_detection.py`: Isolation Forest outlier detection, continuous baseline validation, constant baseline handling.
* `tests/test_actions.py`: Dry-run defaults, non-root enforcement, idempotent review tagging, idle EC2 stopping.
* `tests/test_containment.py`: Concurrency limiting, before/during/after probes, failure rollback.
* `tests/test_pipeline.py`: End-to-end pipeline orchestration, summary text formatting.
* `tests/test_telemetry.py`: Metric collection, sorting, gap-aware handling, CloudWatch limits, metric endpoint scoping.
* `tests/test_web_interactive.py`: Ephemeral session auth, login/logout RAM purging, session isolation, account-scoped restoration.
* `tests/test_multiservice_expansion.py`: EBS volume discovery & gp3 pricing, S3 bucket region filtering, DynamoDB telemetry, strict EC2 idle validation rules, and lab teardown hygiene.

---

## 🔒 Security & Guardrails

* **Zero-Persistence Guarantee**: In-memory ephemeral credentials for web sessions. No access keys are written to SQLite, `.env`, or local logs.
* **Non-Root Requirement**: All API calls verify caller identity via STS and abort immediately if root credentials are detected.
* **Multi-Tag Opt-In**: Autonomous actions strictly require opt-in tags (`CostIntelLab=true`, `AutoOptimize=true`, and `CostIntelIdleStop=true`). Production resources without these tags are never modified.
* **Audit-First State Machine**: Every intended cloud API call is logged to SQLite *before* invocation. Failed or uncertain calls are flagged and never blindly replayed.
* **Reversible Rollbacks**: Throttled functions can be unthrottled in 1 click; stopped instances can be safely restarted.

---

## 📁 Repository Structure

```
Cloud_Cost_Intelligence_System/
├── costintel/                      # Core package
│   ├── __init__.py
│   ├── __main__.py                 # CLI entry point (collect, serve, pipeline)
│   ├── actions.py                  # Guarded cloud optimization policies & tagging
│   ├── containment.py              # Reversible Lambda concurrency throttling
│   ├── detection.py                # ML Anomaly Detection (Isolation Forest)
│   ├── inventory.py                # AWS multi-service scanner (EC2, Lambda, EBS, S3, DynamoDB)
│   ├── pipeline.py                 # End-to-end pipeline orchestrator
│   ├── provisioner.py              # Automated lab resource deployer & teardown
│   ├── store.py                    # SQLite scoped time-series storage
│   ├── telemetry.py                # CloudWatch metric collector (Compute, DynamoDB, S3)
│   ├── web.py                      # Flask web console & ephemeral session gateway
│   ├── static/
│   │   ├── dashboard.css           # Dashboard styles, collapsible layout, light/dark themes
│   │   ├── dashboard.js            # Retractable sidebar (Ctrl+B), SVG chart, modal runner
│   │   └── theme.js                # Instant Dark/Light theme controller with persistence
│   └── templates/
│       ├── dashboard.html          # Real-time FinOps dashboard & Before vs After impact card
│       └── login.html              # Zero-persistence AWS access gateway
├── scripts/
│   ├── baseline_experiment.py      # 111-invocation live CloudWatch experiment
│   ├── run_lambda_lab.py           # Controlled traffic & duration spike stimulator
│   └── verify_containment.py       # Before/During/After containment verification
├── tests/                          # 75 automated unit & integration tests
│   ├── test_actions.py
│   ├── test_containment.py
│   ├── test_detection.py
│   ├── test_foundation.py
│   ├── test_multiservice_expansion.py
│   ├── test_pipeline.py
│   ├── test_telemetry.py
│   └── test_web_interactive.py
├── docs/
│   └── CHECKPOINT.md               # Detailed development log & evidence journal
├── RUNBOOK.md                      # Operational runbook & IAM policies
├── requirements.txt                # Production dependencies
├── requirements-dev.txt            # Development & testing dependencies
└── README.md                       # Platform documentation
```

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
