# Runbook: Snowflake Load Failures

## Warehouse suspended by a resource monitor
1. The error "cannot be resumed because resource monitor ... has exceeded its quota" means the monthly credit quota is used up.
2. Data engineers cannot change resource monitors. Escalate to the platform owner with the monitor name, usage and the business impact (reports not refreshed).
3. The platform owner can raise the quota or move the warehouse to another monitor. After that, rerun the load.
4. Prevention: act on the 80% notification, and review heavy queries using the warehouse query history.

## Authentication errors
Key-pair authentication failures usually mean the public key on the Snowflake user was rotated. Ask the platform team to verify the key fingerprint.
