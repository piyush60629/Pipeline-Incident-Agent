# Runbook: Self-hosted Integration Runtime (SHIR) Offline

## Symptoms
`UserErrorSelfHostedIrOffline` or "integration runtime is offline" for on-premises sources.

## Fix
1. Check the integration runtime status and the last heartbeat time in ADF Monitor.
2. Log in to the SHIR node VM and check the Windows service `DIAHostService` (Microsoft Integration Runtime). After patching reboots it may not start if its startup type is Manual.
3. Start the service, set startup type to Automatic (Delayed Start), and confirm the node shows Online in ADF.
4. Rerun the failed pipelines that use this runtime.
5. Prevention: add a second SHIR node for high availability and an alert on heartbeat loss.
