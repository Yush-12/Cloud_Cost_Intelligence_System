# Connect your AWS account locally

The local website accepts temporary AWS credentials. The CLI also supports existing AWS profiles. Supabase and a hosted backend are not required. The public demo remains separate.

1. From the repository, create a Python 3.12 virtual environment and install `requirements-dev.txt` as described in the README.
2. Run `.venv/Scripts/python -m costintel serve --mode demo --port 5003`. This starts the local connection page; it does not provision resources.
3. Open http://127.0.0.1:5003. Choose the temporary credentials connection, enter the access key ID, secret access key and session token from the same temporary session, and select Mumbai (`ap-south-1`). Refresh the temporary session if it has expired. Do not enter a root identity or share credentials in chat.
4. Start with temporary credentials for a read-only IAM identity. Select **Collect & preview**. Inspect account, region, collection warnings and metric timestamps. An empty account will show empty inventory; missing metrics are not zero usage.
5. To execute, reconnect with temporary credentials for a narrowly scoped mutation identity, collect again, and review each plan's resource, evidence and effect. Select **Execute this plan** only for the intended resource. Previews expire after five minutes and selections are single-use. Failed selections are not automatically retried.
6. Refresh the dashboard to inspect the recorded action. For a contained Lambda or stopped EC2 instance, use its exact action's restoration button. Confirm the recorded result; preserve `.local-data/inventory.sqlite3` for recovery.

## When Lambda containment is eligible

The experimental policy requires a fresh Duration outlier (at most 15 minutes old), 36 consecutive five-minute baseline observations, and a held-out observation at least three times the baseline median and at least 100 ms. It only targets `costintel-lab-workload` in Mumbai. Execution additionally checks a stable 128 MB / 3-second lab function with `CostIntelLab=true` and `CostIntelContainment=true`. Containment sets reserved concurrency to zero and blocks new executions until restoration. A one-hour cooldown and unresolved-action checks prevent repeated containment.

An outlier does not prove waste or justify interrupting a production workload. This policy is deliberately restricted to the opted-in lab. The earlier 111-call experiment is historical evidence and cannot authorize a new action. Do not rerun it merely to populate the dashboard.

## CLI workflow

`python -m costintel.pipeline --mode aws --profile YOUR_PROFILE --account YOUR_ACCOUNT_ID --region ap-south-1 --window-hours 48 --action-type contain-lambda` collects and previews the eligible plan. Explicitly adding `--apply` executes eligible actions after live guard checks. It runs one cycle, not an unattended scheduler. Use `--help` for other action types.

Read-only collection does not require mutation permissions. Containment and restoration require the scoped Lambda GetFunction/GetFunctionConcurrency/PutFunctionConcurrency/DeleteFunctionConcurrency permissions in the lab policies. AccessDenied should be addressed with a resource-scoped policy, not AdministratorAccess.

This workflow has offline test coverage. The new ML-to-containment bridge still requires a separately reviewed live validation. Billing reconciliation and measured monetary savings remain unfinished.
