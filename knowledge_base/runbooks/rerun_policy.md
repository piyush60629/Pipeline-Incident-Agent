# Runbook: Rerun Policy and Approvals

## When a rerun is allowed
- Transient errors (network resets, throttling) that succeeded on retry before, or whose cause is already resolved.
- Late source files that have now fully arrived.
- After a fix has been applied and tested.

## Rules
1. Every production rerun needs approval from the on-call lead, recorded in the incident ticket.
2. Never rerun a batch that failed a critical data quality check until the cause is understood.
3. Watermarks are only updated after a batch fully succeeds, so a rerun of the same batch is safe and idempotent.
4. After a rerun, also rerun downstream pipelines that were skipped, in dependency order.

## Transient network errors
Errors such as "connection was forcibly closed by the remote host" or TCP pre-login handshake failures are usually transient. Check the linked service connection test and the run history: if the same error succeeded on a manual rerun before and the connection test passes now, a rerun is appropriate. Repeated occurrences should be raised with the network team.
