# CostIntel — AWS usage intelligence lab

CostIntel collects AWS resource inventory and CloudWatch observations, stores scoped time-series history in SQLite, and uses an experimental Isolation Forest to flag unusual metric observations. It includes guarded cloud actions and a local web console with dark/light themes, metric charts, filtering, CSV export and action history.

This is a working portfolio project, not a production multi-tenant FinOps service. Usage outliers are not proven cost anomalies. Billing reconciliation and measured dollar savings are not implemented.

## What works

- EC2, Lambda, EBS, S3 and DynamoDB inventory. Optional-source failures are reported instead of silently treated as empty inventory.
- Five-minute EC2/Lambda/DynamoDB metrics; daily S3 StandardStorage size observations. Missing observations remain distinct from zero.
- Gap-aware Isolation Forest with 36 consecutive baseline observations and a held-out sample. Scores are not probabilities. Daily S3 baselines require at least 37 daily observations accumulated over time.
- SQLite snapshots and action journals scoped by local owner, mode, account and region.
- Browser sessions with CSRF protection, one-hour expiry, no default AWS credential fallback, and account-bound SDK sessions. Submitted temporary credentials stay in application memory; external AWS CLI caches have their own lifecycle.
- Preview-only browser pipeline. Guarded execution is available through the local CLI.
- Lambda review tagging, opt-in EC2 stopping/restoration and unused EBS review tagging. EC2 shutdown requires 12 consecutive five-minute CPU observations, the latest no older than 15 minutes, and CostIntelLab/AutoOptimize/CostIntelIdleStop=true. Low CPU alone is only a heuristic, not proof a workload is disposable.
- Recorded Lambda containment/restoration through a separate bounded lab workflow. The pipeline does not automatically connect ML detection to concurrency containment.
- Restoration by exact action ID and account/region; verified results or explicit uncertain states. No bulk audit rewrites.

## Verified live experiment

The project previously completed a 111-invocation Mumbai Lambda experiment spanning 37 five-minute buckets. The held-out Duration observation was 501.7667 ms, with an Isolation Forest score of -0.044124 against 36 baseline buckets. Review tagging was applied separately. A separate manual containment experiment verified execution before containment, throttling during containment, and execution after restoration.

These demonstrate real telemetry, detection and reversible API control. They do not establish autonomous ML remediation or monetary savings. Evidence and credentials are excluded from Git; local evidence is in `.local-data/`.

## Run locally

Requires Python 3.12. From a clone of this repository:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m costintel serve --mode demo --port 5002
```

Open http://127.0.0.1:5002 and choose **Explore the offline demo**. To use your own AWS account, enter your existing AWS CLI profile name, or temporary credentials including a session token, in the local connection page. No root credentials. Start with a dedicated read-only identity. AWS authentication failure never falls back to demonstration data.

Collect without the browser:

```powershell
.venv/Scripts/python -m costintel collect --mode aws --profile YOUR_PROFILE --expected-account YOUR_ACCOUNT_ID --region ap-south-1 --with-metrics --window-hours 48
.venv/Scripts/python -m costintel.pipeline --mode aws --profile YOUR_PROFILE --account YOUR_ACCOUNT_ID --region ap-south-1 --window-hours 48
```

The pipeline command defaults to a dry run. Inspect each plan before explicitly adding `--apply`, using a separately scoped mutation identity. Mutation implementations currently restrict execution to the Mumbai lab; collection supports other regions. Keep the SQLite database for recovery.

## Website and Docker

The Vercel entry point and Docker deployment expose demo mode only. They reject AWS connections. Temporary `/tmp` storage and process-local sessions are not durable hosted multi-account infrastructure. A cold start can require reconnecting the demo.

```powershell
docker compose up --build
```

Docker binds port 5002 internally and publishes only to the host loopback interface. Local AWS mode must remain loopback-only. Browser provisioning and teardown are disabled; use the reviewed Terraform workflow in `infra/lambda-lab`.

## Costs and safety boundary

Free-tier eligibility, credits and resource pricing vary by account. This project cannot guarantee zero charges. Stopping EC2 does not delete EBS storage or eliminate every related charge. No fixed prices or invented savings are displayed as measured results. Do not provision additional workloads solely for the demo; review the Terraform plan and account allowances first.

## Verification

```powershell
.venv/Scripts/python -m pytest -q
```

Offline tests use mocks/stubs, including session isolation, request protection, account-scoped restoration, failed-operation audits, metric gaps and guarded API behavior. They do not certify real IAM permissions or a production deployment.

## Remaining work

1. Hosted user authentication, cross-account role onboarding, durable tenant-isolated storage and background workers.
2. Billing ingestion, pricing-aware estimates and measured before/after reconciliation.
3. A policy-reviewed bridge from detection to autonomous containment, including cooldowns and recovery ownership.
4. Broader multi-service live validation and complete, verified cleanup for any expanded lab.

See [RUNBOOK.md](RUNBOOK.md) for operation and recovery, and [docs/CHECKPOINT.md](docs/CHECKPOINT.md) for the current handoff.
