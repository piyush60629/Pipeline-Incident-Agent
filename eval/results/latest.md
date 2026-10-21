# Agent evaluation results

- Run: 2026-10-21 03:31 UTC · 17 incidents · errors: 0  
- Agent: `openai/gpt-oss-120b` via groq · judge: `qwen/qwen3.8-27b`

| Metric | Score |
|---|---|
| Diagnosis completed | 100.0% |
| Category accuracy | 88.2% |
| Recommended action correct | 94.1% |
| Evidence tool recall | 97.1% |
| Root-cause score (LLM judge) | 94.1% |
| Avg tool calls per incident | 3.4706 |
| Avg seconds per incident | 32.1353 |
| Stopped by the 6-round step budget | 0.0% |
| Runbook search added automatically (agent skipped it) | 52.9% |

| Platform | Incidents | Category | Action | Tool recall | Root cause |
|---|---|---|---|---|---|
| azure | 12 | 91.7% | 91.7% | 95.8% | 95.8% |
| lakehouse | 5 | 80.0% | 100.0% | 100.0% | 90.0% |

## Per-incident results

| Case | Category | Action | Tool recall | Root cause | Tool calls | Note |
|---|---|---|---|---|---|---|
| auth-secret-expired | ✅ authentication | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the expired service principal secret for the specific linked service and date,  |
| permission-role-removed | ✅ permission | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the specific role removal, the affected identity, the target container, and the |
| schema-drift-new-column | ✅ schema_drift | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the extra 'region_code' column as the root cause of the schema mismatch, which  |
| timeout-data-growth | ✅ timeout | ✅ fix_then_rerun | 100% | 1 | 4 | The agent correctly identifies the full rewrite mechanism, the ~36% data volume increase, and the resulting 1- |
| oom-data-skew | ✅ out_of_memory | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the data skew on store_id, the specific store (S-0001), the percentage (41%), t |
| upstream-dependency | ✅ upstream_dependency | ✅ fix_then_rerun | 100% | 1 | 4 | The agent correctly identifies the upstream failure, the specific error (out-of-memory), and the root cause (d |
| duplicate-file-resent | ❌ data_quality | ✅ fix_then_rerun | 100% | 1 | 4 | The agent correctly identifies the duplicate file with the same MD5 hash and the resulting uniqueness check fa |
| late-source-file | ✅ source_file_missing | ✅ rerun | 100% | 0.5 | 3 | The agent correctly identifies the timing mismatch (pipeline started before file arrival) but misses the speci |
| null-rate-source-change | ✅ data_quality | ✅ fix_then_rerun | 100% | 1 | 5 | The agent correctly identifies the 7.4% null rate in the email column caused by the CRM release making it null |
| snowflake-quota | ✅ resource_quota | ❌ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the specific resource monitor, the warehouse, and the cause (quota exhaustion)  |
| transient-network | ✅ transient_network | ✅ rerun | 50% | 1 | 3 | The agent correctly identifies the transient network interruption on the ExpressRoute gateway as the root caus |
| shir-offline | ✅ integration_runtime | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the VM reboot for patching and the failure of the DIAHostService to start as th |
| lakehouse-new-column | ✅ schema_drift | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the extra 'currency' column in the source file as the cause of the MALFORMED_CS |
| lakehouse-text-in-price | ✅ data_quality | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the specific data issue (60 rows of 'N/A' in the price column) and the resultin |
| lakehouse-duplicate-orders | ✅ duplicate_data | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the 800 duplicate order_ids in the orders file as the cause of the ingestion fa |
| lakehouse-truncated-payments | ❌ data_quality | ✅ fix_then_rerun | 100% | 0.5 | 3 | The agent correctly identifies the symptom (malformed CSV record causing parse failure) but misses the underly |
| lakehouse-wrong-start-stage | ✅ upstream_dependency | ✅ rerun | 100% | 1 | 6 | The agent correctly identifies that starting from src.gold skipped the necessary SCD2 dimension build, leading |
