# Agent evaluation results

- Run: 2026-10-19 11:28 UTC · 12 incidents · errors: 3  
- Agent: `openai/gpt-oss-120b` via groq · judge: `qwen/qwen3.8-27b`

| Metric | Score |
|---|---|
| Diagnosis completed | 100.0% |
| Category accuracy | 88.9% |
| Recommended action correct | 100.0% |
| Evidence tool recall | 94.4% |
| Root-cause score (LLM judge) | 88.9% |
| Avg tool calls per incident | 3.2222 |
| Avg seconds per incident | 35.6111 |
| Forced to submit (budget or no tool call) | 100.0% |

## Per-incident results

| Case | Category | Action | Tool recall | Root cause | Tool calls | Note |
|---|---|---|---|---|---|---|
| auth-secret-expired | ✅ authentication | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the expired service principal secret for sp-crm-ingest as the root cause, match |
| permission-role-removed | error | | | | | BadRequestError: Error code: 400 - {'error': {'message': "Tool call validation failed: tool call val |
| schema-drift-new-column | ✅ schema_drift | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the addition of the 'region_code' column as the cause of the schema mismatch. |
| timeout-data-growth | ✅ timeout | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the 1-hour timeout and the steady increase in data volume as the root cause, wh |
| oom-data-skew | ✅ out_of_memory | ✅ fix_then_rerun | 100% | 1 | 2 | The agent correctly identifies data skew on store_id (specifically S-0001) as the cause of the memory exhausti |
| upstream-dependency | ✅ upstream_dependency | ✅ fix_then_rerun | 100% | 1 | 4 | The agent correctly identifies the specific technical cause (data skew on store_id causing OOM) and the locati |
| duplicate-file-resent | ❌ data_quality | ✅ fix_then_rerun | 100% | 0.5 | 4 | The agent correctly identifies the duplicate file and MD5 issue but misses the critical detail that today's ne |
| late-source-file | ✅ source_file_missing | ✅ rerun | 100% | 0.5 | 4 | The agent correctly identifies the timing mismatch (file arrived after pipeline start) but misses the specific |
| null-rate-source-change | ✅ data_quality | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the CRM release changing the email column to nullable as the cause of the manda |
| snowflake-quota | error | | | | | BadRequestError: Error code: 400 - {'error': {'message': "Parsing failed. The model generated output |
| transient-network | ✅ transient_network | ✅ rerun | 50% | 1 | 3 | The agent correctly identifies the transient TCP reset on the ExpressRoute gateway as the root cause, matching |
| shir-offline | error | | | | | BadRequestError: Error code: 400 - {'error': {'message': 'Tool choice is required, but model did not |
