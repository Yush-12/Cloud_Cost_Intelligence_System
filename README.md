# CostIntel · Autonomous Cloud Cost Intelligence & FinOps Engine

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![AWS SDK](https://img.shields.io/badge/AWS-boto3%20%7C%20Free%20Tier-orange.svg)](https://aws.amazon.com/)
[![Machine Learning](https://img.shields.io/badge/ML-Isolation%20Forest%20(scikit--learn)-green.svg)](https://scikit-learn.org/)
[![Tests](https://img.shields.io/badge/tests-68%20passed%20(100%25)-brightgreen.svg)](tests/)
[![Security](https://img.shields.io/badge/Security-Zero--Persistence%20RAM--Only-blueviolet.svg)](#-zero-persistence-security-model)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 🌟 Executive Overview

**CostIntel** is a production-grade Cloud Cost Intelligence & Autonomous FinOps platform connected to live AWS infrastructure (tested in Mumbai `ap-south-1`, operating strictly within AWS Free Tier bounds).

Unlike static billing dashboards that report costs 24 hours after the burn has occurred, CostIntel operates at the telemetry layer:
1. Ingests high-frequency 5-minute metric streams across **EC2 Compute**, **Lambda Serverless**, **S3 Storage**, and **DynamoDB**.
2. Evaluates time-series behavior using an unsupervised **Isolation Forest** machine learning model to detect true usage anomalies (such as duration runaway loops and idle instances).
3. Automatically executes **reversible, policy-guarded optimizations via Cloud APIs** (e.g. throttling runaway compute, halting idle instances, and applying governance review tags).
4. Visualizes verified **Before vs After run-rate reductions** on a real-time FinOps web dashboard.

---

## 🚀 Key Innovations & Capabilities

### 1. Zero-Persistence Ephemeral Authentication (`/login`)
* **Enterprise Gateway**: Enter temporary AWS Access Keys or connect via local IAM profiles.
* **RAM-Only Lifecycle**: Credentials exist strictly in Python process memory for the active browser session. **Keys are never persisted to SQLite, `.env`, or disk.**
* **Non-Root Enforcement**: Validates credentials via `sts:GetCallerIdentity` on connect; root credentials are automatically rejected.
* **1-Click Disconnect**: Instantly purges credentials from memory and returns to the gateway.

### 2. Multi-Service Resource Inventory & Run-Rate Pricing
* Discovers and monitors resources across all 4 target AWS services:
  * **Compute (EC2)**: Live state tracking (`running`, `stopped`), instance types (`t2.micro`), and hourly-to-monthly cost translation (`$8.47/mo`).
  * **Serverless (Lambda)**: Runtime configuration, memory allocation (128MB), and invocation cost modeling.
  * **Storage (S3)**: Bucket inventory, storage class tagging, and lifecycle management.
  * **Database (DynamoDB)**: Active tables with pay-per-request and free-tier allocation tracking.
* **Granular Cost Breakdown**: The resource inventory displays exact monthly run-rates alongside identified FinOps savings opportunities.

### 3. ML Anomaly Detection (Isolation Forest)
* **Gap-Aware Telemetry**: Never fabricates zeroes for absent CloudWatch datapoints; missing intervals remain distinct from zero consumption.
* **Continuous Baseline Tail**: Learns normal usage patterns from 36 consecutive 5-minute observation buckets (3-hour window).
* **Deterministic Outlier Scoring**: Uses Scikit-Learn's `IsolationForest` to assign anomaly scores to held-out evaluation points, detecting duration spikes (e.g., 501.8ms vs 8.2ms normal) and idle compute patterns.

### 4. Guarded Autonomous Cloud API Optimization Engine
Four policy-driven remediation categories with full reversibility:
1. **Lambda Runaway Throttling**: Safely limits function concurrency to `0` via `lambda:PutFunctionConcurrency`, stopping cost burn while returning HTTP 429 to callers. Reversible via `lambda:DeleteFunctionConcurrency`.
2. **Stopping Idle Compute**: Automatically halts instances with average CPU $< 2.0\%$ via `ec2:StopInstances` (saving $8.47/month), with 1-click recovery via `ec2:StartInstances`.
3. **Idempotent Review Tagging**: Applies immutable audit tags (`CostIntelReview: review-<sha256>`) for human-in-the-loop review.
4. **EBS Volume Optimization**: Identifies unattached EBS storage volumes (state: `available`) and tags them for reclamation.

### 5. Automated Live AWS Lab Provisioner & Safe Teardown
* **1-Click Lab Provisioning (`[ 🚀 Provision Lab Resources ]`)**: Automatically creates or verifies tagged bounded test assets (`costintel-lab-workload`, `costintel-lab-idle-ec2`, S3 cache bucket, DynamoDB table).
* **Live Anomaly Stimulation (`[ ⚡ Inject Anomaly Spike ]`)**: Injects controlled 500ms duration spikes into the live Lambda workload to demonstrate real-time ML detection.
* **1-Click Safe Teardown (`[ 🗑️ Teardown Lab ]`)**: Stops compute instances and resets concurrency to baseline so **zero unexpected AWS charges accrue**.

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
              GetMetricStatistics) │                   │  TagResource)
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
                    │  - Live Telemetry Scrubber   │
                    │  - Before vs After Lifecycle │
                    │  - Lab Control Action Bar    │
                    └──────────────────────────────┘
```

---

## 💻 Tech Stack & Design Standards

| Layer | Technologies & Standards |
|---|---|
| **Backend & Orchestrator** | Python 3.12, Flask, Boto3 (AWS SDK), Botocore |
| **Machine Learning** | Scikit-Learn (`IsolationForest`), NumPy |
| **Data & Telemetry Store** | SQLite3 (durable scoped snapshots, zero external telemetry leaks) |
| **Frontend UI/UX** | Vanilla JS, Modern CSS3, HTML5 |
| **Cloud Target** | AWS Free Tier (`ap-south-1` Mumbai): EC2, Lambda, S3, DynamoDB, CloudWatch, STS |
| **Verification & Testing** | Pytest, Boto3 Stubber, Moto (68 offline unit & integration tests) |

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
   * Click one of the **Developer & Evaluator Presets** (`costintel-deploy` or `Offline Demo Sandbox`).
2. **Inspect Resources & Costs**:
   * Review discovered assets across EC2, Lambda, S3, and DynamoDB.
   * View the estimated monthly run-rate breakdown and savings potential per asset.
3. **Run the Lab & Remediation**:
   * Click **`[ 🚀 Provision Lab Resources ]`** to deploy or verify test workloads in AWS.
   * Click **`[ ⚡ Inject Anomaly Spike ]`** to stimulate a 500ms slow duration spike.
   * Click **`[ ▶ Auto-Remediate ]`** to trigger Isolation Forest analysis and execute live cloud API containment.
   * View the **Before vs After Optimization Impact** card showing verified run-rate reduction (-$14.31/mo).
   * Click **`[ ↺ Restore Baseline ]`** to revert concurrency throttles and restore normal operation.
4. **Sign Out**:
   * Click **`[ Disconnect ]`** in the topbar to purge all credentials from RAM.

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
   * **Stage 1 (Before)**: Succeeded with `200 OK`.
   * **Stage 2 (During)**: Concurrency throttled to `0` via `PutFunctionConcurrency`. Callers safely received `429 TooManyRequestsException`.
   * **Stage 3 (After)**: Unreserved concurrency restored via `DeleteFunctionConcurrency`. Invocation succeeded with `200 OK`.
   * Durable audit record committed to SQLite before and after API invocation.

---

## 🧪 Automated Test Suite

All 68 unit and integration tests run completely offline with stubbed AWS clients:

```powershell
.venv/Scripts/python -m pytest tests -q --basetemp=".local-data/pytest-tmp"
```

```text
....................................................................     [100%]
68 passed in 52.96s
```

### Test Coverage Highlights:
* `tests/test_foundation.py`: Snapshot persistence, scope isolation, viewer states, pagination stubbing, root credential rejection.
* `tests/test_detection.py`: Isolation Forest outlier detection, continuous baseline validation, constant baseline handling.
* `tests/test_actions.py`: Dry-run defaults, non-root enforcement, idempotent review tagging, idle EC2 stopping.
* `tests/test_containment.py`: Concurrency limiting, before/during/after probes, failure rollback.
* `tests/test_pipeline.py`: End-to-end pipeline orchestration, summary text formatting.
* `tests/test_web_interactive.py`: Ephemeral session auth, login/logout RAM purging, lab provisioner, and teardown endpoints.

---

## 🔒 Security & Guardrails

* **Zero-Persistence Guarantee**: In-memory ephemeral credentials for web sessions. No access keys are written to SQLite, `.env`, or local logs.
* **Non-Root Requirement**: All API calls verify caller identity via STS and abort immediately if root credentials are detected.
* **Multi-Tag Opt-In**: Autonomous actions strictly require opt-in tags (`CostIntelLab=true` and `AutoOptimize=true`). Production resources without these tags are never modified.
* **Audit-First State Machine**: Every intended cloud API call is logged to the local SQLite database *before* invocation. Failed or uncertain calls are flagged and never blindly replayed.
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
│   ├── inventory.py                # AWS multi-service scanner & cost breakdown
│   ├── pipeline.py                 # End-to-end pipeline orchestrator
│   ├── provisioner.py              # Automated lab resource deployer & teardown
│   ├── store.py                    # SQLite scoped time-series storage
│   ├── telemetry.py                # CloudWatch metric collector
│   ├── web.py                      # Flask web console & session gateway
│   ├── static/
│   │   ├── dashboard.css           # Tactile design system (Zero AI-slop)
│   │   ├── dashboard.js            # Live scrubber, SVG chart, lab triggers
│   │   └── theme.js                # Flash-free Dark/Light theme controller
│   └── templates/
│       ├── dashboard.html          # Real-time FinOps dashboard & impact card
│       └── login.html              # Zero-persistence AWS access gateway
├── scripts/
│   ├── baseline_experiment.py      # 111-invocation live CloudWatch experiment
│   ├── run_lambda_lab.py           # Controlled traffic & duration spike stimulator
│   └── verify_containment.py       # Before/During/After containment verification
├── tests/                          # 68 automated unit & integration tests
│   ├── test_actions.py
│   ├── test_containment.py
│   ├── test_detection.py
│   ├── test_foundation.py
│   ├── test_pipeline.py
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
