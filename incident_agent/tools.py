"""Tools the agent can call while investigating. All are read-only.

The docstrings are what the LLM sees when it decides which tool to use, so they say
when each tool is useful. Outputs are compact JSON, cut to MAX_TOOL_OUTPUT_CHARS,
because every investigation round resends the whole conversation to the model.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.tools import tool

from . import data_store
from .config import MAX_TOOL_OUTPUT_CHARS
from .knowledge import Source, default_knowledge_base


def _compact(payload: Any) -> str:
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        text = text[: MAX_TOOL_OUTPUT_CHARS - 20] + "...(truncated)"
    return text


def _safe(fn: Callable[[], Any]) -> str:
    try:
        return _compact(fn())
    except data_store.NotFound as exc:
        return _compact({"error": str(exc)})


@tool
def get_pipeline_run(run_id: str) -> str:
    """Get the details of one pipeline run: pipeline, failed activity, error code and message,
    timings, source file, linked services, Databricks job and upstream run id (if any).
    Always call this first when you have a run id."""

    return _safe(lambda: data_store.get_run(run_id))


@tool
def get_recent_runs(pipeline_name: str, limit: int = 7) -> str:
    """List the most recent runs of a pipeline (newest first) with status, duration, rows read,
    source file and file MD5. Use it to see whether a failure is new or recurring, whether the
    same error succeeded on a rerun before, whether durations are growing, or whether the same
    file content (same MD5) was loaded before."""

    def fetch() -> list[dict]:
        keep = ("run_id", "status", "start", "duration_min", "rows_read", "triggered_by", "source_file",
                "file_md5", "error_code")
        return [{k: r[k] for k in keep if k in r} for r in data_store.recent_runs(pipeline_name, limit)]

    return _safe(fetch)


@tool
def check_linked_service(name: str) -> str:
    """Check a linked service (connection): authentication method, credential or secret expiry,
    connection test result, recent IAM/role changes, integration runtime status, and Snowflake
    warehouse / resource monitor usage. Use it for authentication, permission (403), connection,
    integration runtime and quota errors."""

    return _safe(lambda: data_store.linked_service(name))


@tool
def compare_source_schema(dataset: str) -> str:
    """Compare the columns expected by the pipeline with the columns in the latest source file
    for a dataset (for example 'stores', 'customers'), including column nullability changes,
    null rates and source release notes. Use it for column count/type errors and for data
    quality failures that may come from a source change."""

    def fetch() -> dict:
        schema = dict(data_store.source_schema(dataset))
        expected, received = schema.get("expected_columns", []), schema.get("received_columns", [])
        schema["added_columns"] = [c for c in received if c not in expected]
        schema["missing_columns"] = [c for c in expected if c not in received]
        return schema

    return _safe(fetch)


@tool
def get_job_metrics(job_name: str) -> str:
    """Get Databricks job metrics for a notebook job (for example 'nb_gold_sales_fact'): recent
    durations and input rows, cluster size, failed stage, task input sizes (median vs max, which
    reveals data skew), skewed keys and relevant Spark settings. Use it for timeouts and
    out-of-memory errors."""

    return _safe(lambda: data_store.job_metrics(job_name))


@tool
def check_landing_zone(path: str) -> str:
    """Look up files in the landing zone by full path or folder (for example
    'landing/inventory/inventory_20261019.csv' or 'landing/sales/'): arrival time, size, MD5 hash,
    sender notes and the agreed arrival SLA. Use it for missing, late or duplicate files."""

    return _safe(lambda: data_store.landing_files(path))


@tool
def search_knowledge(query: str, source: Source = "all") -> str:
    """Search the team's runbooks (fix procedures) and past incident reports. Use specific words
    from the error and the evidence, for example 'AuthorizationPermissionMismatch role removed'.
    source can be 'all', 'runbooks' or 'incidents'. Returns the top 3 sections with citations."""

    results = default_knowledge_base().search(query, source=source, k=3)
    if not results:
        return _compact({"results": [], "note": "No matching knowledge base sections."})
    return _compact({"results": [{"citation": s.citation, "text": s.text[:650]} for s, _ in results]})


INVESTIGATION_TOOLS = [
    get_pipeline_run,
    get_recent_runs,
    check_linked_service,
    compare_source_schema,
    get_job_metrics,
    check_landing_zone,
    search_knowledge,
]
TOOL_NAMES = [t.name for t in INVESTIGATION_TOOLS]
