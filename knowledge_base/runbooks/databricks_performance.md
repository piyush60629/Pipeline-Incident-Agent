# Runbook: Databricks Timeouts, Out-of-Memory and Data Skew

## Activity timeout
1. Compare recent run durations with the activity timeout policy. A steady increase usually means data growth, not a one-off issue.
2. Short term: raise the activity timeout (for example from 60 to 90 minutes) so the batch can finish, then rerun after approval.
3. Long term: partition large Gold tables by date and use incremental MERGE instead of full rewrites; consider a larger job cluster.
4. The incremental watermark is only updated after the whole batch succeeds, so a rerun reprocesses the same data safely.

## Out of memory (ExecutorLostFailure, Java heap space, container killed)
1. Open the failed stage and compare the median and maximum task input sizes.
2. If one task reads far more data than the median, the cause is data skew on the join key, not cluster size.
3. Fixes for skew, in order: enable AQE skew join handling (`spark.sql.adaptive.skewJoin.enabled=true`), broadcast the smaller dimension table if it is under a few hundred MB, or salt the skewed key.
4. Increasing executor memory alone usually only delays the failure.
5. After the fix, rerun the failed batch and then any downstream pipelines that were waiting on it.

## Downstream pipelines
Gold pipelines wait for their Silver dependencies. When a dependency check fails, fix the upstream run first; rerunning the downstream pipeline alone will fail again.
