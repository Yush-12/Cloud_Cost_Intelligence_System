# CostIntel

CostIntel is a cloud usage intelligence lab for AWS. It discovers resources, collects CloudWatch observations, stores scoped time-series history, and uses Isolation Forest to flag unusual usage. The local dashboard lets an operator inspect evidence, review eligible actions, execute one action at a time, and verify or restore the result.

**Current scope:** the local website connects to a real AWS account with temporary credentials and supports guarded actions. The [hosted website](https://cloud-cost-intelligence-system.vercel.app/) offers both a clearly labeled synthetic tour and durable read-only AWS monitoring backed by Neon Postgres; hosted cloud actions remain disabled until durable recovery is implemented. An [optional billing connector](docs/BILLING_OPTION.md) is disabled by default because AWS charges for Cost Explorer API requests. Metric outliers and account-wide billed cost do **not** establish resource-level savings; billing reconciliation is not implemented.

## Run the website locally

Requires Python 3.12. On Windows, from the repository root:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m costintel serve --mode demo --port 5003
```

Open http://127.0.0.1:5003/login. Choose **Explore the offline demo** or enter temporary AWS credentials from a dedicated IAM identity. Root credentials are rejected. The login page explains how to request a temporary credential set. The server binds to loopback by default; do not expose the local AWS mode to the public internet.

The [local workflow](docs/LOCAL_WORKFLOW.md) covers connection, collection, plan review, execution, restoration, and the AWS CLI alternative. Temporary credentials remain in server memory for up to one hour or until disconnect; the SQLite database contains telemetry and action records, not those credentials. Preserve `.local-data/inventory.sqlite3` when recovering an action.

## What works

| Capability | Current behavior |
| --- | --- |
| Inventory | EC2, Lambda, EBS, S3, and DynamoDB discovery; partial coverage is reported. |
| Telemetry | Five-minute compute and DynamoDB observations; daily S3 StandardStorage size. Missing data is kept distinct from zero. |
| Detection | Isolation Forest evaluates a held-out sample after 36 consecutive baseline observations. Its score is not a probability. |
| Review | The local browser collects and creates five-minute, single-use, account-bound action previews. |
| Actions | Lambda review tagging; opted-in EC2 stop and restore; unused EBS review tagging; opted-in Mumbai lab Lambda concurrency containment and restore. Each action rechecks its guards against AWS. |
| Recovery | Intent and prior state are recorded before changes; exact action IDs and final AWS state are used for restoration. Uncertain outcomes are reported rather than silently retried. |

Lambda containment is limited to `costintel-lab-workload` in Mumbai. It requires a fresh Duration outlier, a large increase over the baseline, explicit resource tags, and a one-hour cooldown. Setting reserved concurrency to zero blocks new invocations until restoration; it is not a measured savings result. EC2 stopping also requires fresh, continuous idle CPU evidence and explicit opt-in tags. No EBS volume deletion is automated.

## Verified AWS lab evidence

A prior Mumbai experiment invoked the lab Lambda 111 times across 37 five-minute buckets. Its held-out Duration observation was 501.7667 ms, with an Isolation Forest score of -0.044124 against 36 baseline buckets. A separate manual containment experiment verified execution before containment, throttling during containment, and execution after restoring the original concurrency. These are historical results, not current AWS state or proof of monetary savings. Local evidence in `.local-data/` is excluded from Git.

The new detection-to-containment bridge has offline tests but has not yet been validated live against AWS. Historical observations are too old to authorize a new action.

The hosted website was live-verified in Mumbai on 2026-11-04 with temporary credentials from `costintel-reader`: it discovered the existing lab Lambda, collected five CloudWatch metric series, and displayed a fresh invocation datapoint. EBS, S3, and DynamoDB inventory initially reported IAM denials; after the reader policy was corrected and a fresh session established, the user confirmed all warnings disappeared. This verifies telemetry and inventory, not hosted cloud actions or billed cost accuracy.

## Deployments and costs

The Vercel entry point requires Neon Postgres and a stable session secret for AWS workspaces. Its offline demo uses stateless synthetic data, makes no AWS calls, and cannot execute cloud actions. See [hosted setup](docs/HOSTED_SETUP.md). Docker remains an offline demo and publishes port 5002 on the host loopback interface:

```powershell
docker compose up --build
```

The bounded Terraform lab is in [infra/lambda-lab](infra/lambda-lab/README.md). Review the plan, account, region, credits, and current allowances before provisioning. Free-tier eligibility varies by account; this project cannot guarantee zero charges. Browser provisioning and teardown are disabled.

## Validate

```powershell
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest -q
```

Tests use AWS mocks and stubs. They check isolation, request protection, telemetry gaps, plan guards, action audits, and restoration, but do not certify IAM policy deployment or live AWS behavior. See [RUNBOOK.md](RUNBOOK.md) for operating and recovery steps.

## Next work

1. Validate the detection-to-containment bridge in the opted-in lab with fresh evidence and exact restoration.
2. Live-validate the opt-in billing connector only if API charges are acceptable, then implement defensible before/after impact before reporting dollar savings.
3. Add durable action journals and guarded execution to the hosted site after separately validating recovery semantics.
