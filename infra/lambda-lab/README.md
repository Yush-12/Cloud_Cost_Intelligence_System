# Bounded Lambda telemetry lab

Deployment candidate; not deployed. These controlled scenarios generate real
CloudWatch observations, not evidence of ML accuracy or measured cost savings.

## Resources

Four managed resources: one Mumbai Lambda (Python 3.12, ARM64, 128 MB, 3-second
timeout), its execution role, a log-writing inline policy, and a log group with
one-day retention. The handler has no SDK calls, outbound requests or recursion.
There is no schedule, function URL, event source, VPC or provisioned concurrency.

The runner makes 10 sequential synchronous calls by default, at most 20 per run.
It verifies the account, ARN, lab tag, timeout, memory and ready state. SDK retries
are disabled; unexpected errors stop the run. Repeating the script creates more
usage. It is not a global spending cap. At 20 calls and a 3-second timeout, configured
handler execution is bounded at 60 seconds / 7.5 GB-seconds, but initialization,
logs, repeated runs and other callers can add usage. Verify remaining credits and
allowances before deployment; no zero-cost guarantee is claimed.

Reserved concurrency is unset because small reservations can fail on low-quota
accounts. The runner's sequential behavior does not constrain other clients.

## Separate deployment access

Keep the existing `costintel-reader` identity read-only. An administrator can
create a separate trusted deployment identity, attach the AWS-managed
`SignInLocalDevelopmentAccess` policy for browser login, and attach
`deploy-policy.json` after replacing every `ACCOUNT_ID` with the target account ID.
Use a distinct login profile and a process profile called `costintel-deploy`, as
described in the root README. Do not create long-lived access keys.

The deployment policy is a reviewable starting policy, not yet live-tested against
the provider. Although role and resource names are restricted, the identity can
edit the execution role's inline policy and therefore grant that role additional
permissions. This is trusted deployment access, not a permissions boundary or a
policy to grant to hosted monitoring connections. No AdministratorAccess is needed.

## Validate, plan, deploy

Install Terraform 1.7+; the development copy is
`.local-data/tools/terraform/terraform.exe`. Copy `terraform.tfvars.example` to
`terraform.tfvars` in this directory and fill the expected account ID and deployment
profile. Run from the repository root:

If Terraform cannot execute the Windows credential-process provider even though
the AWS CLI can, `aws_profile = ""` enables the standard temporary environment
credential chain. Supply short-lived credentials only to that Terraform process;
never put keys in tfvars, state configuration, source files, or terminal output.
The expected account restriction still applies. Saved plans made this way also
require temporary environment credentials when applied.

```powershell
terraform -chdir=infra/lambda-lab init
terraform -chdir=infra/lambda-lab fmt -check
terraform -chdir=infra/lambda-lab validate
terraform -chdir=infra/lambda-lab test
terraform -chdir=infra/lambda-lab plan -out=lab.tfplan
```

Review four managed resource additions. The archive data source only writes a local
ZIP. The provider refuses a different AWS account. Do not import or replace any
pre-existing resources with matching names without verifying ownership. After
reviewing permissions, costs and the plan:

```powershell
terraform -chdir=infra/lambda-lab apply lab.tfplan
.venv/Scripts/python -m scripts.run_lambda_lab --profile costintel-deploy --expected-account YOUR_ACCOUNT_ID --count 10 --scenario baseline
```

Wait for the current five-minute metric bucket to finish and CloudWatch to publish
data (publication may lag). Collect using the read-only profile:

```powershell
.venv/Scripts/python -m costintel collect --mode aws --profile costintel --expected-account YOUR_ACCOUNT_ID --with-metrics
.venv/Scripts/python -m costintel serve --mode aws --account YOUR_ACCOUNT_ID
```

Check the lab ARN and actual invocation/duration samples. Missing samples are not
zero activity. If more than ten resources exist, inspect the collection-limit
message. Run `slow` and `error` scenarios in separate five-minute windows for clear
comparisons. Baseline sleeps 50 ms, slow sleeps 500 ms, error deliberately raises.
A handful of points is not a trained anomaly baseline.

If the experiment happened more than an hour ago, add `--window-hours 48` to the
collection command; this only reads historical CloudWatch samples. For the first
baseline lab run, AWS reported one five-minute bucket containing 10 invocations,
0 errors, approximately 51.5 ms average duration, 0 throttles and concurrency 1.

## Bounded baseline experiment

Preview locally (no AWS access):

```powershell
.venv/Scripts/python -m scripts.baseline_experiment
```

The plan uses 108 baseline invocations in 36 consecutive five-minute buckets,
followed by three slow invocations in the next bucket. It takes about three hours.
It creates no new resources. Existing function, log and API usage remains subject
to the account's allowances. The workload is fixed at 128 MB and a three-second
timeout; 111 invocations have at most 333 seconds of configured execution timeout
in aggregate (41.625 GB-seconds), excluding other charges or billing rules.

After explicitly authorizing the plan and signing in with the deployment profile:

```powershell
.venv/Scripts/python -m scripts.baseline_experiment --execute --profile costintel-deployer --expected-account YOUR_ACCOUNT_ID
```

Keep the computer awake. Ctrl+C stops future calls; an in-flight call may finish.
Start a fresh deployment login immediately before a long experiment: an already
authenticated profile can reach its overall session expiry mid-run even if its
short-term credentials refresh. The first timed run stopped after 33 baseline
buckets (99 calls) at the STS identity check with ExpiredToken; CloudWatch confirmed
99 calls and zero errors. Its slow phase never ran, and no detection success is
claimed. Preserve failed-run journals and obtain approval before replacement calls.
The local journal records attempts before invoking. Existing journals are refused
to prevent replay. Inspect failures before planning a fresh run; no catch-up calls
are issued after missed slots. Authentication expiry also stops the run.
Collect with `--window-hours 48` after metrics arrive. Sparse or constant metrics
may still be ineligible for detection. Duration is the primary experimental signal;
three slow calls are a controlled test, not evidence of actual billed waste.

## Lab containment demonstration

This explicit, manually initiated test blocks new executions by setting reserved
concurrency to zero, then restores the previous reservation (including unreserved).
It is a runtime control demonstration, not autonomous ML remediation or measured
billing savings. It needs the additional scoped `containment-policy.json` with
`ACCOUNT_ID` replaced, plus the explicit lab tag `CostIntelContainment=true`.

```powershell
.venv/Scripts/python -m scripts.verify_containment --profile YOUR_DEPLOYMENT_PROFILE --account YOUR_ACCOUNT_ID
# After reviewing the preview and opting in:
.venv/Scripts/python -m scripts.verify_containment --profile YOUR_DEPLOYMENT_PROFILE --account YOUR_ACCOUNT_ID --execute
```

The test makes three synchronous probe attempts: before, while contained, after
restoration. Normally two execute and one is throttled. Durable intent precedes
mutation, and restoration runs in a finally block even after a lost API response.
A process crash can still interrupt restoration; inspect `containment_actions`
in the local database and restore the recorded ID with:

```powershell
.venv/Scripts/python -m costintel.containment restore --profile YOUR_DEPLOYMENT_PROFILE --account YOUR_ACCOUNT_ID --action-id RECORDED_ACTION_ID
```

No automatic retry is performed on an uncertain containment request. Changed
concurrency blocks restoration to avoid overwriting another operator's setting.
AWS does not provide compare-and-swap for this API; use one operator for this lab.
Preserve the database until restoration succeeds, and remove the opt-in tag when
finished. Current in-flight executions are not cancelled by this control.

## Teardown steps

Stop invoking first. Teardown permanently deletes the lab logs; export any desired
experiment evidence before applying the destroy plan.

```powershell
terraform -chdir=infra/lambda-lab plan -destroy -out=cleanup.tfplan
terraform -chdir=infra/lambda-lab apply cleanup.tfplan
```

Keep Terraform state until teardown succeeds. Recollect inventory to verify the
function is gone. Local telemetry remains available. Revoke deployment access or
end its session when finished. State, ZIPs, plans and real tfvars are Git-ignored.

References: [Lambda Terraform resource](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_function),
[AWS concurrency behavior](https://docs.aws.amazon.com/lambda/latest/dg/lambda-concurrency.html).
