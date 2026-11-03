# CostIntel operator runbook

## Local workspace

1. Install requirements in a Python 3.12 virtual environment.
2. Run `python -m costintel serve --mode demo --port 5002`.
3. Open the local URL and choose the offline demo or connect your AWS account with temporary credentials, including a session token. Never use root.
4. Collect and preview. Check account, region, snapshot time, missing metrics and inventory warnings before interpreting results.
5. Collection does not apply plans. Review an eligible plan, then use **Execute this plan** with a scoped mutation identity. Previews expire in five minutes. Refresh to inspect the action and restore its prior state. See [the local workflow](docs/LOCAL_WORKFLOW.md) for the complete sequence and CLI alternative.

A browser session expires after one hour or server restart. Disconnect removes it from the application registry. CLI credential caches are managed outside the application. The local server is for a trusted single-user machine, not shared hosting.

## Execution and restoration

Actions require identity validation and resource opt-in. EC2 stop also requires CostIntelIdleStop=true, fresh continuous CPU evidence, and observed final state. Tags do not by themselves prove a workload is safe to interrupt. No EBS deletion is automated.

Keep `.local-data/inventory.sqlite3`. Intent is written before API mutations. If a call fails or times out, inspect the recorded uncertain status and actual AWS state before attempting recovery. Do not delete journals or replay uncertain actions blindly.

Restore only the exact recorded action from the correct account and region. Lambda restoration uses its recorded prior concurrency, which may be reserved or unreserved. EC2 start waits for a verified running state. Restoration can fail; never infer success from a button click or an accepted API request.

The separate `scripts.verify_containment` workflow is for an explicitly reviewed lab experiment. Its completed historical journal must not be replayed. The earlier 111-call experiment has finished; no further calls are needed to review existing evidence.

## Infrastructure

Browser provision/teardown endpoints are disabled. Use the scoped Terraform lab under `infra/lambda-lab`, inspect the plan, verify account/region and review allowances before any apply. Terraform destroy needs its state file and a reviewed removal plan. Stopping compute alone is not complete cleanup.

## Hosted website

Vercel uses Neon Postgres for scoped telemetry history and encrypted temporary AWS sessions. The public demo is removed from its login page. Hosted cloud actions and restoration remain disabled until their durable recovery journal is built and validated. See [hosted setup](docs/HOSTED_SETUP.md). Docker remains an offline demo on localhost:5002.

## Troubleshooting

- 401: connect a workspace again; there is no default credential fallback.
- 403 on submission: refresh the page for a current CSRF token. Cross-origin requests are rejected.
- AWS login failure: refresh the local AWS login and verify the selected profile; do not paste credentials into chat.
- Missing metric: not equivalent to zero. Inspect CloudWatch publication cadence and resource activity.
- S3 metrics: daily StandardStorage size only, not all storage classes or request activity.
- No plans: evidence may be stale, incomplete or not eligible. Do not weaken thresholds just to generate a demo action.
- Dollar impact: not implemented; usage reduction and review tags are not measured savings.

## Offline validation

Run `python -m pytest -q`. No live AWS mutations are required for the test suite. Browser review uses a separate `.local-data/ui-review.sqlite3` database so the original experiment audit is preserved.
