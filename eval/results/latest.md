# Agent evaluation results

- Run: 2026-10-19 11:37 UTC · 3 incidents · errors: 0  
- Agent: `openai/gpt-oss-20b` via groq · judge: `qwen/qwen3.8-27b`

| Metric | Score |
|---|---|
| Diagnosis completed | 100.0% |
| Category accuracy | 100.0% |
| Recommended action correct | 33.3% |
| Evidence tool recall | 100.0% |
| Root-cause score (LLM judge) | 100.0% |
| Avg tool calls per incident | 2.6667 |
| Avg seconds per incident | 23.3 |
| Stopped by the 6-round step budget | 0.0% |

## Per-incident results

| Case | Category | Action | Tool recall | Root cause | Tool calls | Note |
|---|---|---|---|---|---|---|
| permission-role-removed | ✅ permission | ✅ escalate | 100% | 1 | 4 | The agent correctly identifies the specific IAM role removal, the affected identity, the date, and the resulti |
| snowflake-quota | ✅ resource_quota | ❌ fix_then_rerun | 100% | 1 | 2 | The agent correctly identifies the resource monitor RM_MONTHLY_REPORTING hitting its 500-credit quota as the c |
| shir-offline | ✅ integration_runtime | ❌ rerun | 100% | 1 | 2 | The agent correctly identifies the offline SHIR, the Windows patch reboot, and the failure of the DIAHostServi |
