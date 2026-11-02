# Cloud Cost Intelligence System — Operational Runbook

## Overview

This runbook covers day-to-day operations, testing, troubleshooting, and emergency recovery for the **Cloud Cost Intelligence System** (`costintel`).

The system supports two execution modalities:
1. **Real AWS Mode**: Connects via temporary IAM credentials to an AWS Free Tier / Tutorial account in Mumbai (`ap-south-1`). Gathers live inventory and CloudWatch telemetry, detects cost anomalies via Isolation Forest ML, and safely executes policy-driven optimizations via cloud APIs with strict opt-in guardrails.
2. **Demo Mode**: Zero-credential, completely offline local evaluation mode suitable for automated tests, demonstrations, and development without AWS accounts.

---

## Command Quick Reference

| Action | Command | Notes |
|---|---|---|
| **Run Full FinOps Pipeline (AWS)** | `python -m costintel pipeline --mode aws --profile YOUR_PROFILE --account YOUR_ACCOUNT_ID --with-metrics` | Discovers inventory, ingests CloudWatch metrics, runs ML detection, and generates dry-run optimization plans |
| **Apply Verified Optimizations (AWS)** | `python -m costintel pipeline --mode aws --profile YOUR_PROFILE --account YOUR_ACCOUNT_ID --with-metrics --apply` | Requires explicit opt-in tags (`CostIntelLab=true` and `AutoOptimize=true`) |
| **Run Full FinOps Pipeline (Demo)** | `python -m costintel pipeline --mode demo` | Offline mode, zero AWS credentials |
| **Collect Real AWS Inventory & Metrics** | `python -m costintel collect --mode aws --profile YOUR_PROFILE --expected-account YOUR_ACCOUNT_ID --with-metrics --window-hours 1` | Ingests live 5-minute CloudWatch telemetry into SQLite |
| **Start Local Web Dashboard** | `python -m costintel serve --mode aws --account YOUR_ACCOUNT_ID --port 5002` | Opens on `http://127.0.0.1:5002` (interactive charts, dark/light theme, audit panels) |
| **Start Web Dashboard (Demo)** | `python -m costintel serve --mode demo --port 5002` | Runs against local snapshot store |
| **Preview Review Tagging Plan** | `python -m costintel.actions --account YOUR_ACCOUNT_ID` | Identifies duration / metric outliers |
| **Apply Review Tag to Lambda** | `python -m costintel.actions --account YOUR_ACCOUNT_ID --profile YOUR_PROFILE --apply` | Commits idempotent review tag `CostIntelReview` |
| **Preview Lambda Concurrency Containment** | `python -m costintel.containment preview --profile YOUR_PROFILE --account YOUR_ACCOUNT_ID` | Reads current reserved concurrency |
| **Apply Lambda Containment (Throttle to 0)** | `python -m costintel.containment apply --profile YOUR_PROFILE --account YOUR_ACCOUNT_ID` | Requires `CostIntelContainment=true` |
| **Restore Lambda Concurrency** | `python -m costintel.containment restore --profile YOUR_PROFILE --account YOUR_ACCOUNT_ID --action-id <UUID>` | Restores original concurrency setting |
| **Run Automated Pytest Suite** | `python -m pytest tests -q --basetemp=".local-data/pytest-tmp"` | 61 automated unit & integration tests |

---

## Autonomous & Policy-Controlled Optimizations

The system enforces four distinct categories of safe cloud optimizations with multi-layer guardrails:

### 1. Limiting Function Concurrency (`containment.py`)
- **Target**: Runaway serverless functions (high error rates or unbounded execution spikes).
- **Execution**: Sets reserved concurrency to `0` via `lambda:PutFunctionConcurrency`, stopping new execution invocations.
- **Guardrails**: Resource must match Mumbai lab ARN; tagged with `CostIntelLab=true` and `CostIntelContainment=true`.
- **Rollback**: Records exact prior reserved concurrency (or unreserved state) in SQLite table `containment_actions`. Restores via `lambda:DeleteFunctionConcurrency` or original allocation.

### 2. Tagging Unreviewed Assets & Anomalies (`actions.py`)
- **Target**: Functions or instances with statistically flagged runtime metric outliers.
- **Execution**: Applies tag `CostIntelReview: review-<sha256>` via `lambda:TagResource` or `ec2:CreateTags`.
- **Guardrails**: Tagged with `CostIntelLab=true` and `AutoOptimize=true`; refuses to overwrite existing review tags.
- **Rollback**: Operators inspect tagged resources and clear the tag after review.

### 3. Stopping Idle Compute Instances (`actions.py` - `stop_idle_instance`)
- **Target**: EC2 instances in `running` state with average CPU utilization below threshold (e.g. `< 2.0%`).
- **Execution**: Calls `ec2:StopInstances` with target state `stopped`.
- **Guardrails**:
  - Non-root IAM caller identity matching expected account.
  - Mumbai region (`ap-south-1`) only.
  - Instance must be explicitly tagged with `CostIntelLab=true` and `AutoOptimize=true`.
  - Intent recorded in SQLite table `optimization_actions` before SDK call.
- **Rollback / Recovery**: Call `apply_start_instance(store, session, action_id)` to restart the instance via `ec2:StartInstances`.

### 4. Cleaning & Tagging Unused Volumes (`actions.py` - `tag_unused_volume`)
- **Target**: Orphaned EBS volumes in `available` (unattached) state incurring idle storage charges.
- **Execution**: Tags the orphaned volume for scheduled cleanup / review.
- **Guardrails**: Requires opt-in tags (`CostIntelLab=true` and `AutoOptimize=true`).

---

## AWS IAM Security & Least Privilege

The system rejects root AWS credentials by design (`sts:GetCallerIdentity` pre-flight check).

### Required IAM Users:
1. **`costintel-reader`** (Read-Only):
   - Attached Policy: `infra/inventory-read-policy.json`
   - Actions: `ec2:DescribeInstances`, `lambda:ListFunctions`, `cloudwatch:GetMetricStatistics`.
   - Condition: Scoped strictly to `ap-south-1`.
2. **`costintel-deployer`** (Lab Deployment & Safe Remediation):
   - Attached Policies:
     - `infra/lambda-lab/deploy-policy.json`
     - `infra/lambda-lab/containment-policy.json`
   - Scoped strictly to the Mumbai lab function ARN.

---

## Troubleshooting Guide

### 1. `ExpiredToken` or Session Timeout
- **Symptom**: AWS SDK calls return `ClientError: The security token included in the request is expired`.
- **Remedy**: Re-authenticate the temporary session profile:
  ```powershell
  aws login --profile costintel-login --region ap-south-1
  ```
  Verify active identity:
  ```powershell
  aws sts get-caller-identity --profile costintel
  ```

### 2. Port Collision (e.g. Port 5001 or 5002 in use)
- **Symptom**: `OSError: [WinError 10048] Only one usage of each socket address is normally permitted`.
- **Remedy**: Find the listening process and specify an alternative port:
  ```powershell
  netstat -ano | findstr :5002
  Stop-Process -Id <PID> -Force
  python -m costintel serve --mode aws --account YOUR_ACCOUNT_ID --port 5003
  ```

### 3. Missing Telemetry Buckets
- **Symptom**: Metric explorer reports "Missing — no datapoints" or "More history needed".
- **Explanation**: CloudWatch only emits datapoints when activity occurs. Absent buckets mean zero invocations occurred during that 5-minute interval. The system intentionally treats absent data as missing (unknown) rather than assuming 0.
- **Remedy**: For evaluation of the Isolation Forest ML model, a minimum of 36 consecutive 5-minute buckets (3 hours) is required.

### 4. Pytest Windows Temp Directory Permission Error
- **Symptom**: `PermissionError: [WinError 5] Access is denied: ...pytest-of-username`.
- **Remedy**: Always specify the local temporary base directory:
  ```powershell
  python -m pytest tests -q --basetemp=".local-data/pytest-tmp"
  ```
