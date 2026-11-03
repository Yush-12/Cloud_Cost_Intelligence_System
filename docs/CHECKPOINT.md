# Current checkpoint — 2026-11-03

This checkpoint supersedes earlier claims that all hackathon deliverables are complete.

## Latest review and implementation

Reviewed Antigravity commit ee4b3a5 (following 060de3c). Kept its multi-service discovery/telemetry, chart/filter/CSV features, collapsible sidebar, and dark/light design. It partially addressed session isolation and action-ID restoration, but retained unauthenticated credential fallback, bulk audit rewriting and unsupported dollar claims.

Implemented locally after that commit:
- Mandatory browser connection, CSRF/same-origin checks, one-hour session expiration, root rejection, per-session AWS SDK, no fallback identity.
- Hosted Vercel/Docker demo rejects AWS connections. Local AWS server remains loopback-only.
- Browser pipeline is preview-only; CLI guarded execution remains available. Browser provisioning/stimulation and unverified SDK provisioning/teardown are disabled.
- Bulk restore removed. Exact action ID, account and region required; restoration failures do not mark unrelated actions restored.
- EC2 stop requires fresh 12 consecutive five-minute CPU observations and CostIntelIdleStop=true in addition to existing lab/AutoOptimize tags. Stop/start verifies observed final state. EBS tagging rechecks attachment state and verifies the applied tag.
- Unsupported prices/savings removed. Optional inventory failures visible, S3 location failures are not assigned to the active region, DynamoDB pagination, S3 completed UTC-day windows.
- Demo includes explicitly synthetic metric history to exercise the explorer with no AWS calls. These are not the actual experiment results.
- README and RUNBOOK now state actual capabilities, costs boundary and remaining work.
- Docker network binding fixed and host port restricted to localhost.

## Validation

81 offline tests passed in 13.21 seconds. Final test output: `.local-data/review-tests.txt`.
Browser review: local demo at http://127.0.0.1:5003 using separate `.local-data/ui-review.sqlite3`. Login, metric chart, dark/light themes and offline preview flow inspected. Original experiment database is preserved. No live AWS reads or mutations were needed for this review pass.

## Existing live evidence — do not replay

Mumbai Lambda `costintel-lab-workload`, account 695313591289.
- Original 10 baseline calls completed.
- First scheduled run stopped after 99 successful calls due to credential expiry.
- Replacement completed 111 calls / 37 CloudWatch buckets. Duration 501.7667ms, Isolation Forest score -0.044124 against 36 baseline buckets.
- Review tag applied; AutoOptimize restored false.
- Separate manual concurrency containment verified executed -> throttled -> executed; original unreserved concurrency restored. Temporary containment opt-in removed.
- Containment action: 62c5ab7e-ddc8-4bd2-9d7e-142e762d238d.
- Original audit database `.local-data/inventory.sqlite3`; experiment journals `.local-data/experiments/`.

These are historical verified outcomes, not a claim about current live AWS state. Do not rerun experiments, delete journals, or blindly replay uncertain actions.

## Next priorities

1. Hosted user authentication and cross-account role onboarding with durable tenant-isolated storage/workers. Public AWS login is intentionally unavailable until these exist.
2. Billing reconciliation and measurable impact. Dollar savings are not implemented.
3. Policy-reviewed ML-to-containment integration with cooldowns and recovery responsibility; current Lambda containment demonstration is manually initiated.
4. Expanded live multi-service validation and verified cleanup before re-enabling broader provisioning.
5. Responsive/browser coverage beyond the current desktop checks; production deployment remains unverified.

No usage quota is visible to Codex. Resume from this file and current git diff; do not assume a previous server process is still running.

## Login redesign completed

Replaced the unstyled legacy login with a dedicated responsive connection page (`login.css` / `login.js`). Demo entry is prominent; local profile is the default AWS method; temporary credentials are separate with required fields and masked secret/token. Removed personal-account presets, added accessible validation/loading/error messages, a visible theme control, and accurate public-demo copy. Browser verified desktop (1280px), phone (390px), dark/light modes, required-field validation, method switching and demo navigation. Nine focused web tests pass. No real AWS credentials or calls were used in UI verification.
