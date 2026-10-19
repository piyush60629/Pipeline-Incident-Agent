"""The platforms the agent can investigate. Each one has its own tools, instructions and rerun action.

* azure:     an invented Azure Data Factory + ADLS + Databricks + Snowflake platform (synthetic data).
* lakehouse: the Retail Analytics Lakehouse, a PySpark Medallion pipeline whose REAL run logs and
             report snapshots are read from lakehouse/monitoring/.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import data_store, lakehouse_store
from .config import MAX_TOOL_ROUNDS
from .lakehouse_tools import LAKEHOUSE_TOOLS
from .tools import INVESTIGATION_TOOLS

AZURE_PROMPT = f"""You are an on-call data platform engineer investigating a failed pipeline run on an Azure
Data Factory + ADLS Gen2 + Databricks + Snowflake platform.

How to work:
1. If you have a run id, call get_pipeline_run first.
2. Gather evidence with the specific tools for the error (several tools per round is fine):
   credentials, permissions, runtimes or quotas -> check_linked_service; column or data quality errors ->
   compare_source_schema; timeouts or out-of-memory -> get_job_metrics; missing, late or duplicate
   files -> check_landing_zone and get_recent_runs; transient-looking errors -> get_recent_runs and
   check_linked_service; upstream dependency failures -> get_pipeline_run on the upstream run id.
3. Call search_knowledge at least once with the key error words and your suspected cause, to find the
   team's runbook fix and similar past incidents.
4. Only state facts you saw in tool results. Use at most {MAX_TOOL_ROUNDS} tool rounds.
5. When you have enough evidence, stop calling tools and reply with a short summary of the root cause,
   the key evidence, the fix and whether a rerun, a fix first, or another team is needed."""

LAKEHOUSE_PROMPT = f"""You are an on-call data engineer investigating a failed run of the Retail Analytics Lakehouse,
a PySpark Medallion pipeline (raw CSV -> validation -> Bronze -> CDC -> Silver -> SCD2 dimensions -> enriched
facts -> Gold -> Gold quality -> analytics) started with run_pipeline.py. The tools read the real run log and
report snapshots of each run.

How to work:
1. If you have a run id, call get_lakehouse_run first: note the failed stage, the exception, and whether the
   run used start_from to skip earlier stages.
2. Gather evidence for the error (several tools per round is fine): CSV parsing, type or duplicate errors in
   ingestion -> check_source_files (all files, then the suspect dataset); missing input paths or a run that
   skipped stages -> get_layer_outputs; unclear errors -> get_stage_log; data quality -> get_validation_report
   and get_gold_quality; new versus recurring -> get_recent_lakehouse_runs.
3. Call search_knowledge at least once with the key error words and your suspected cause, to find the
   lakehouse runbook fix.
4. Only state facts you saw in tool results. Use at most {MAX_TOOL_ROUNDS} tool rounds.
5. When you have enough evidence, stop calling tools and reply with a short summary of the root cause
   (which file, column or stage, and why), the key evidence, the fix, and the stage to rerun from."""


@dataclass(frozen=True)
class Environment:
    key: str
    label: str
    system_prompt: str
    tools: list
    get_run: Callable[[str], dict]
    not_found: type[Exception]
    trigger_rerun: Callable[..., dict]
    rerun_question: Callable[[dict], str]


def _azure_question(run: dict) -> str:
    return f"Rerun pipeline '{run['pipeline']}' (run {run['run_id']})?"


def _lakehouse_question(run: dict) -> str:
    return f"Rerun the lakehouse batch {run['batch_date']} with `{lakehouse_store.rerun_command(run['run_id'])}`?"


ENVIRONMENTS: dict[str, Environment] = {
    "azure": Environment(
        key="azure",
        label="Azure data platform (synthetic incidents)",
        system_prompt=AZURE_PROMPT,
        tools=INVESTIGATION_TOOLS,
        get_run=data_store.get_run,
        not_found=data_store.NotFound,
        trigger_rerun=data_store.trigger_rerun,
        rerun_question=_azure_question,
    ),
    "lakehouse": Environment(
        key="lakehouse",
        label="Retail Analytics Lakehouse (real PySpark run logs)",
        system_prompt=LAKEHOUSE_PROMPT,
        tools=LAKEHOUSE_TOOLS,
        get_run=lakehouse_store.get_run,
        not_found=data_store.NotFound,
        trigger_rerun=lakehouse_store.trigger_rerun,
        rerun_question=_lakehouse_question,
    ),
}
DEFAULT_ENVIRONMENT = "azure"


def get_environment(key: str | None) -> Environment:
    key = (key or DEFAULT_ENVIRONMENT).strip().lower()
    if key not in ENVIRONMENTS:
        raise ValueError(f"Unknown environment '{key}'. Choose from: {', '.join(ENVIRONMENTS)}")
    return ENVIRONMENTS[key]


def tool_names(key: str | None) -> list[str]:
    return [t.name for t in get_environment(key).tools]


def run_label(environment: str, run: dict[str, Any]) -> str:
    """A short label of a failed run for pickers."""

    if environment == "lakehouse":
        return f"batch {run['batch_date']}  ·  {run.get('failed_stage') or '?'}  ·  {(run.get('error_type') or 'error').rsplit('.', 1)[-1]}"
    return f"{run['pipeline']}  ·  {run['error_code']}  ·  {run['start'][:16].replace('T', ' ')}"
