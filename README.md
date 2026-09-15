# ☁️ Cloud Cost Intelligence System (FinOps Simulation Engine)

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