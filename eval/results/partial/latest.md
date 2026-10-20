# Agent evaluation results

- Run: 2026-10-20 02:27 UTC · 5 incidents · errors: 0  
- Agent: `openai/gpt-oss-120b` via groq · judge: `qwen/qwen3.8-27b`

| Metric | Score |
|---|---|
| Diagnosis completed | 100.0% |
| Category accuracy | 100.0% |
| Recommended action correct | 100.0% |
| Evidence tool recall | 100.0% |
| Root-cause score (LLM judge) | 100.0% |
| Avg tool calls per incident | 3.4 |
| Avg seconds per incident | 23.3 |
| Stopped by the 6-round step budget | 0.0% |
| Runbook search added automatically (agent skipped it) | 100.0% |

## Per-incident results

| Case | Category | Action | Tool recall | Root cause | Tool calls | Note |
|---|---|---|---|---|---|---|
| lakehouse-new-column | ✅ schema_drift | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the extra 'currency' column as the cause of the schema mismatch and resulting M |
| lakehouse-text-in-price | ✅ data_quality | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the specific data issue ('N/A' in price column) and the resulting error (Number |
| lakehouse-duplicate-orders | ✅ duplicate_data | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the 800 duplicate order_ids in the orders file as the root cause of the ingesti |
| lakehouse-truncated-payments | ✅ corrupt_source_file | ✅ fix_then_rerun | 100% | 1 | 3 | The agent correctly identifies the malformed record, the missing newline, and the resulting parse error, which |
| lakehouse-wrong-start-stage | ✅ upstream_dependency | ✅ rerun | 100% | 1 | 5 | The agent correctly identifies that starting from src.gold skipped the necessary earlier stages, resulting in  |
