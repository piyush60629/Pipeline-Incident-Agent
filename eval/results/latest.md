# Agent evaluation results

- Run: 2026-10-19 11:16 UTC · 12 incidents · errors: 1  
- Agent: `openai/gpt-oss-120b` via groq · judge: `qwen/qwen3.8-27b`

| Metric | Score |
|---|---|
| Diagnosis completed | 100.0% |
| Category accuracy | 81.8% |
| Recommended action correct | 90.9% |
| Evidence tool recall | 95.5% |
| Root-cause score (LLM judge) | 86.4% |
| Avg tool calls per incident | 2.5455 |
| Avg seconds per incident | 21.6091 |
| Forced to submit (budget or no tool call) | 81.8% |

## Per-incident results

| Case | Category | Action | Tool recall | Root cause | Tool calls | Note |
|---|---|---|---|---|---|---|
| auth-secret-expired | ✅ authentication | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the expired service principal secret for the specific linked service as the roo |
| permission-role-removed | error | | | | | RateLimitError: Error code: 429 - {'error': {'message': "Request too large for model `qwen/qwen3.8-2 |
| schema-drift-new-column | ✅ schema_drift | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the addition of the 'region_code' column as the cause of the schema mismatch, m |
| timeout-data-growth | ✅ timeout | ✅ fix_then_rerun | 100% | 1 | 2 | The agent correctly identifies the increased data volume and the resulting timeout as the root cause, matching |
| oom-data-skew | ✅ out_of_memory | ✅ fix_then_rerun | 100% | 1 | 2 | The agent correctly identifies the core issue of data skew on store_id causing an OutOfMemoryError, which alig |
| upstream-dependency | ❌ out_of_memory | ✅ fix_then_rerun | 100% | 0.5 | 3 | The agent correctly identifies the technical cause (data skew on store_id causing OOM) but fails to identify t |
| duplicate-file-resent | ✅ duplicate_data | ✅ fix_then_rerun | 100% | 0.5 | 3 | The agent correctly identifies the duplicate file and transaction_id conflict but misses the critical detail t |
| late-source-file | ✅ source_file_missing | ✅ rerun | 100% | 0.5 | 2 | The agent correctly identifies the immediate technical symptom (missing file) but misses the specific business |
| null-rate-source-change | ❌ schema_drift | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the schema change making the email column nullable as the root cause for the da |
| snowflake-quota | ✅ resource_quota | ❌ fix_then_rerun | 100% | 1 | 2 | The agent correctly identifies the specific resource monitor, the quota exhaustion, and the resulting warehous |
| transient-network | ✅ transient_network | ✅ rerun | 50% | 1 | 2 | The agent correctly identifies the transient TCP reset on ExpressRoute as the root cause, matching the referen |
| shir-offline | ✅ integration_runtime | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the VM reboot for patching and the DIAHostService manual startup type as the ca |
