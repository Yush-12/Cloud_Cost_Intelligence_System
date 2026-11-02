# Project checkpoint

## Completed Milestones

- Real Mumbai Lambda lab deployed; original 10 invocations verified in CloudWatch.
- Local inventory, telemetry, SQLite history and experimental Isolation Forest.
- Bounded 111-invocation live AWS experiment completed: 37 CloudWatch buckets collected; Duration outlier flagged at 501.8ms (score -0.044124).
- Idempotent review tag applied to Lambda on real AWS (`review-1e612ccf93287060506c60d7`).
- Live reversible containment verified on AWS Lambda: before probe executed, during probe throttled (429), and after probe executed upon restoration to unreserved concurrency.
- Multi-service cloud optimizations implemented with strict multi-tag opt-in (`CostIntelLab=true` and `AutoOptimize=true`):
  1. Limiting function concurrency (`containment.py`)
  2. Tagging unreviewed assets (`actions.py`)
  3. Stopping idle compute resources with reversible restart (`actions.py`)
  4. Cleaning / tagging unused EBS volumes (`actions.py`)
- Unified end-to-end FinOps intelligence pipeline orchestrator (`costintel/pipeline.py`) supporting demo and real AWS modes with dry-run default.
- Modern responsive dashboard with Dark/Light theme, interactive gap-aware SVG charts, and multi-service accountability audit panels.
- Comprehensive operational runbook (`RUNBOOK.md`) and rewritten architecture documentation (`README.md`).
- 74 automated unit and integration tests passing offline (`74 passed in ~50s`).

## Current Boundary

LATEST STATUS: All core deliverables specified in the hackathon statement are implemented, tested, and verified.
Active viewer running locally on `http://127.0.0.1:5002/`.
All temporary artifacts remain strictly ignored in `.local-data/`.
No outstanding runtime actions in AWS. AutoOptimize and containment tags remain false / cleaned up.
This is manually initiated runtime containment/restoration, not ML-autonomous
optimization or proven dollar savings. Next priorities: reproducible user
onboarding, dashboard polish, honest public deployment, billing/estimated usage
distinction, and connecting policy decisions to execution safely. Deadline unchanged.

LATEST DEVELOPMENT: implemented costintel/containment.py and
scripts/verify_containment.py. Seven focused tests pass, including before/blocked/
restored probes and rollback after lost apply response. No live containment has
run. User was asked to add CostIntelLabContainment inline policy to deployer with
lambda:PutFunctionConcurrency and lambda:DeleteFunctionConcurrency scoped ONLY
to arn:aws:lambda:ap-south-1:695313591289:function:costintel-lab-workload.
Template: infra/lambda-lab/containment-policy.json. Await user's policy completion.

After policy update: verify deployer login; preview live concurrency; set ONLY lab
CostIntelContainment=true under existing completion authorization; execute
`python -m scripts.verify_containment --profile costintel-deploy --account
695313591289 --execute`. Three probe attempts planned (normally two execute, one
throttles). Durable before-state in containment_actions, journal at
.local-data/experiments/containment.jsonl. Runner restores in finally, but inspect
state and recover using recorded ID if interrupted. Remove containment opt-in tag
after verification. Do not replay existing journal or uncertain action. This is
manual lab containment, NOT autonomous ML runtime remediation or dollar savings.

CURRENT RESULT (supersedes earlier running/login notes): replacement FINISHED,
111 calls, 37/37 CloudWatch buckets. Duration held-out slow sample at 11:05 UTC
was 501.7667 ms, outlier score -0.044124, using 36 prior consecutive buckets.
Other four metrics report insufficient variation (expected constant workload).
Saved evidence .local-data/replacement-evidence.json and collection
.local-data/replacement-collection.json. Do not invoke more workload calls.

Guarded review tagging implemented in costintel/actions.py; dry-run default,
exact Mumbai lab scope, non-root identity, explicit opt-in, no overwrite,
durable intent and no replay on uncertain API result. Applied successfully in AWS
under user's broad project completion authorization. Review tag value
review-1e612ccf93287060506c60d7; action ID in .local-data/review-plan.json.
AutoOptimize briefly set true for this demonstration, then restored FALSE.
Audit lives in SQLite review_actions table. This tags for review; NO runtime
optimization or savings claim. Viewer restarted on port 5002, process 4004.
61 tests passed; added audit scope assertions also passed focused tests.

NEXT DEVELOPMENT: improve dashboard/packaging and reproducible onboarding;
implement runtime optimization with measured before/after evidence before claiming
full hackathon completion. Hosted multi-account auth and persistent hosted storage
remain undone. Deadline 2026-11-03 09:00 IST. User authorized work to completion;
do not ask routine permissions again. No usage-quota visibility: checkpoint at
meaningful boundaries. Do not confuse legacy Vercel simulation with rebuilt app.

REPLACEMENT LAUNCHED: fresh login completed as costintel-deployer. Lab verification
passed. Background process 16952; journal replacement-20261102.jsonl under
.local-data/experiments; logs .local-data/replacement.stdout.log and
.local-data/replacement.stderr.log. First batch 2026-11-02 08:05:30 UTC
(13:35:30 IST), final batch 11:05:30 UTC (16:35:30 IST). Do not start another run.
First batch confirmed complete: 3 invocations, zero function errors. Remaining
108 calls are scheduled in the same running process.
57 tests pass. Added offline `python -m costintel.experiment --journal PATH`
to compare journal and scoped stored CloudWatch evidence. Next collect metrics
after 16:40 IST with reader profile; inspect run completion before any new calls.

LATEST: User authorized a replacement experiment and requests completion by
2026-11-03 09:00 IST, working around five-hour usage refresh windows. Quota is not
visible to the agent. Fresh deploy login started in exec session 38169, awaiting
user sign-in as costintel-deployer. Verify STS identity after login completes.
Use NEW journal .local-data/experiments/replacement-20261102.jsonl. The original
journal must remain untouched. Replacement 111 calls are authorized; no extra
confirmation needed. Runner now preflights identity and lab before scheduling.

Deadline priorities after replacement launch: preserve experiment evidence;
add guarded lab-only execution/audit with dry-run default; improve dashboard and
reproducible local setup; document hosted multi-account gaps honestly. Do not
claim the legacy Vercel demo is a live multi-tenant AWS product. Hosted onboarding
requires actual authentication and tenant isolation before accepting cloud roles.

UPDATE 2026-11-02 07:55 UTC: experiment STOPPED. 33 completed baseline batches,
99 successful invocations, no slow calls. Batch index 33 failed at STS identity
verification with ExpiredToken BEFORE Lambda invocation. Do not restart/replay.
The deployment login now explicitly reports session expired. Reader collection
succeeded: snapshot 444ebe88-ce08-4a0e-b795-e3dc65ee6a37. CloudWatch confirms 33
buckets of three calls, zero errors and throttles, peak concurrency one. Duration
bucket averages range 51.5433–63.1133 ms. Detector reports insufficient_data with
32 prior buckets and one evaluation bucket. Preserve these observations.

NEXT: fresh deployment login immediately before any replacement timed experiment.
The interrupted three-hour experiment cannot be completed across missed buckets
under the current continuous-baseline rule. A replacement run would exceed the
original 111-call authorization in aggregate; obtain approval for replacement
calls first. Do not lower the detector minimum to manufacture a successful result.
The earlier process/schedule information below is historical, not an active run.

The user explicitly authorized the 111-call experiment. It was launched on
2026-11-02 as background process 19944 with profile costintel-deploy, verified as
costintel-deployer. The journal exists with a planned event; no completed batches
were observed at the checkpoint. Do NOT start another runner.

Schedule: first batch 2026-11-02 04:50:30 UTC (10:20:30 IST); final slow batch
07:50:30 UTC (13:20:30 IST). Journal: .local-data/experiments/baseline.jsonl.
Logs: .local-data/experiment.stdout.log and .local-data/experiment.stderr.log.
User requires no out-of-pocket spending. Free account has user-reported credits;
this does not establish that every service/API is free.

Proposed execution: 36 baseline buckets, three calls per bucket, followed by one
slow bucket of three calls: 111 total sequential Lambda invocations over roughly
three hours, only costintel-lab-workload in ap-south-1. No new cloud resources or
schedulers. Uses the existing lab verification and no SDK invocation retries.

## Resume with “continue”

1. Read this file and git status. Check .local-data/experiments for a journal before
   starting any experiment. Never replay an uncertain or existing run.
2. Read the UPDATE above: run stopped after 99 calls. Do not launch a duplicate.
3. Keep the computer awake. A stopped run is evidence to inspect, not to replay.
4. After CloudWatch publishes the completed buckets, collect 48-hour telemetry
   using the reader profile. Evaluate the slow sample against earlier baseline.
5. Record actual results, including gaps or missed detection; never claim model
   accuracy from synthetic tests. No optimization is enabled.

Codex cannot see the account usage quota. This file is the recovery point if a
session stops. The experiment has stopped; no new calls are scheduled.
