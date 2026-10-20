# Agent evaluation results

- Run: 2026-10-20 02:21 UTC · 5 incidents · errors: 0  
- Agent: `openai/gpt-oss-120b` via groq · judge: `qwen/qwen3.8-27b`

| Metric | Score |
|---|---|
| Diagnosis completed | 100.0% |
| Category accuracy | 100.0% |
| Recommended action correct | 100.0% |
| Evidence tool recall | 100.0% |
| Root-cause score (LLM judge) | 100.0% |
| Avg tool calls per incident | 2.8 |
| Avg seconds per incident | 35.44 |
| Stopped by the 6-round step budget | 0.0% |

## Per-incident results

| Case | Category | Action | Tool recall | Root cause | Tool calls | Note |
|---|---|---|---|---|---|---|
| lakehouse-new-column | ✅ schema_drift | ✅ fix_then_rerun | 100% | 1 | 2 | The agent correctly identifies the extra 'currency' column in the products CSV as the cause of the schema mism |
| lakehouse-text-in-price | ✅ data_quality | ✅ fix_then_rerun | 100% | 1 | 2 | The agent correctly identifies the specific data issue ('N/A' in price column) and the resulting error (Number |
| lakehouse-duplicate-orders | ✅ duplicate_data | ✅ fix_then_rerun | 100% | 1 | 2 | The agent correctly identifies the 800 duplicate order_id rows as the root cause of the ingestion failure, mat |
| lakehouse-truncated-payments | ✅ corrupt_source_file | ✅ fix_then_rerun | 100% | 1 | 2 | The agent correctly identifies the malformed record, the missing newline, and the resulting parse error, which |
| lakehouse-wrong-start-stage | ✅ upstream_dependency | ✅ fix_then_rerun | 100% | 1 | 6 | The agent correctly identifies that starting from src.gold skipped the SCD2 dimension build, leading to the mi |
