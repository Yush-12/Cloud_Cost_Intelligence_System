# Cloud Cost Intelligence System — FinOps Simulation Runbook

## Quick Start
| Task | Command |
|---|---|
| Start unified app (Dashboard UI + Pipeline) | `python app.py` |
| Start dashboard API & UI only | `python app.py --api-only` |
| Start pipeline runner only | `python app.py --pipeline-only` |
| Run full pipeline cycle once | `python src/pipeline.py --once` |
| Preview rollback actions | `python src/rollback.py --dry-run` |
| Roll back all tracked actions | `python src/rollback.py` |
| Emergency full rollback | `python src/rollback.py --all-resources` |
| Run offline test suite | `pytest tests/ -v` |

---

## Architecture & Simulation Engine

The system operates as an autonomous FinOps intelligence engine powered by:
- **`src/data_store.py`**: Thread-safe in-memory data store mimicking DynamoDB (`InMemoryTable`) and core AWS services (EC2, Lambda, CloudWatch, S3, STS).
- **`src/synthetic_data.py`**: High-signal synthetic data generator populating realistic 7-day multi-service billing telemetry and resource state.
- **`src/aws_clients.py`**: Client layer providing unified simulated clients without network or credential dependencies.

---

## How to Tune Detection Thresholds

**File:** `.env` or `src/anomaly_detector.py`
**Variable:** `CONFIDENCE_THRESHOLD = 0.85`

| Value | Effect |
|---|---|
| 0.70 | More anomalies detected, higher sensitivity |
| 0.85 | Default — balanced precision and recall |
| 0.95 | Fewer detections, very high precision only |

Change in `.env` and restart or trigger a new cycle — no code changes required.

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
This restarts only the EC2 instances stopped by this system, removes concurrency caps from capped Lambdas, and clears review tags, updating the audit records to `rolled_back`.

**Option B — Emergency full rollback:**
```powershell
python src/rollback.py --all-resources
```

---

## Troubleshooting Common Questions

| Scenario | Explanation & Fix |
|---|---|
| Are AWS credentials required? | **No.** The system runs 100% in-memory with realistic simulated telemetry. Zero AWS credentials needed. |
| Dashboard shows empty panels | The system auto-seeds on first API call. Click "⚡ Run Optimization Cycle" or refresh the page. |
| Port 5000 already in use | Change `FLASK_PORT=5001` in `.env` or run `python app.py --port 5001`. |
| Pipeline error `could not convert string to float` | Resolved: All numeric conversions use `safe_float()` / `safe_int()`. |
