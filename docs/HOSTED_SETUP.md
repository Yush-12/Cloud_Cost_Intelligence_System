# Hosted AWS website setup

The Vercel site uses the same connection form and dashboard as localhost. It connects to AWS using temporary credentials submitted over HTTPS. Hosted collection and anomaly detection are read-only; cloud actions remain local until durable hosted action recovery is implemented.

1. In the existing Vercel project, connect a **free Neon Postgres** database through Storage. Confirm the integration added `DATABASE_URL` or `POSTGRES_URL` to the **Production** environment. Check the plan and usage limits in your own account before confirming a paid option.
2. Add `COSTINTEL_SESSION_SECRET` to Production. Generate a private random value in PowerShell with `[Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(48))`. Keep it in Vercel settings; never commit or share it. Keep the same value across redeployments, or existing sessions will be invalidated.
3. Deploy this repository revision to the connected Vercel project. Vercel environment variable changes require a new deployment. The function is configured for Mumbai (`bom1`).
4. Open the production URL. The page should show the temporary AWS credential form and no offline demo button. Use a dedicated read-only IAM identity with temporary STS credentials. Root credentials are rejected.
5. Check account and region on the dashboard. Select **Collect & Preview** to store CloudWatch observations and run detection. Hosted collection is bounded to five metric resources and a six-hour window per request. Repeat collection over time to build the 36-point baseline.

Neon holds encrypted temporary AWS credentials for up to one hour, scoped by a random session ID. The browser cookie carries only signed identifiers, not AWS keys. Stored telemetry is scoped by a random workspace ID plus account and region. A browser reconnect retains its workspace ID while its signed cookie remains valid; disconnect clears it. The database connection and session secret are required on every deployment; missing configuration returns an explicit setup error rather than exposing an unsafe demo or in-memory AWS connection.

There is no hosted background worker, billing reconciliation, or hosted cloud mutation yet. If AWS identity verification succeeds but inventory cannot be saved, check Neon availability and IAM read permissions. If the function times out, inspect Vercel logs and reduce collection scope before increasing duration or cost. Validate two separate browser sessions and a cold start before inviting others.
