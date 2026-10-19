"""Tools for investigating REAL runs of the Retail Analytics Lakehouse (PySpark Medallion pipeline).

All read-only. They read the run logs and report snapshots the lakehouse writes for every run
(see lakehouse_store.py). Outputs are compact JSON for the same reason as tools.py.
"""

from __future__ import annotations

from langchain_core.tools import tool

from . import lakehouse_store as store
from .tools import _safe, search_knowledge

STAGE_SUMMARY_KEYS = ("name", "module", "status", "duration_seconds", "exit_code")


def _short(text: str | None, limit: int) -> str | None:
    return None if text is None else (text if len(text) <= limit else text[: limit - 3] + "...")


@tool
def get_lakehouse_run(run_id: str) -> str:
    """Get one lakehouse pipeline run: batch date, status, how it was started (start_from stage, if
    it skipped earlier stages), each stage's status and exit code, and for the failed stage the
    exception type, message and key error lines. Always call this first when you have a run id."""

    def fetch() -> dict:
        run = store.get_run(run_id)
        stages = []
        for stage in run["stages"]:
            item = {k: stage.get(k) for k in STAGE_SUMMARY_KEYS}
            if stage["status"] == "FAILED":
                item["error_type"] = stage.get("error_type")
                item["error_message"] = _short(stage.get("error_message"), 300)
                item["root_error"] = _short(stage.get("root_error"), 400)
                item["key_error_lines"] = [_short(line, 220) for line in (stage.get("key_error_lines") or [])[-4:]]
            stages.append(item)
        keep = ("run_id", "pipeline", "batch_date", "status", "started_at", "duration_seconds", "start_from",
                "triggered_by", "failed_stage")
        return {**{k: run.get(k) for k in keep}, "stages": stages}

    return _safe(fetch)


@tool
def get_stage_log(run_id: str, stage: str) -> str:
    """Read the console log of one stage of a lakehouse run (stage name or module, for example
    'src.incremental'): the last log lines and every error line, including Spark 'Caused by' lines.
    Use it when the error message alone does not show the cause."""

    def fetch() -> dict:
        run = store.get_run(run_id)
        wanted = stage.strip().lower()
        match = next((s for s in run["stages"] if wanted in (s["module"].lower(), s["name"].lower())), None)
        if match is None:
            return {"error": f"No stage '{stage}' in {run_id}. Stages: {', '.join(s['module'] for s in run['stages'])}"}
        if match["status"] != "FAILED":
            return {"stage": match["module"], "status": match["status"], "note": "Only failed stages keep a log."}
        return {
            "stage": match["module"],
            "key_error_lines": [_short(line, 220) for line in match.get("key_error_lines") or []],
            "log_tail": [_short(line, 160) for line in (match.get("log_tail") or [])[-14:]],
        }

    return _safe(fetch)


@tool
def get_recent_lakehouse_runs(limit: int = 7) -> str:
    """List the most recent lakehouse runs (newest first) with batch date, status, duration, failed
    stage and error type. Use it to see whether the previous batches succeeded and whether the
    failure is new or recurring."""

    return _safe(lambda: store.recent_runs(limit))


@tool
def check_source_files(run_id: str, dataset: str = "") -> str:
    """Check the raw source CSV files as they were for a lakehouse run: file manifest status and whether
    the manifest registered the file for this batch (false if it was not re-delivered OR ingestion never ran
    for the batch), size, row count, rows with the wrong number of columns, whether the file ends cleanly,
    the last row, columns added or missing versus the Bronze schema, values that do not match the schema's
    numeric types, and duplicate primary keys. Datasets: customers, products, orders, order_items,
    payments. Leave dataset empty for all files."""

    def fetch() -> dict:
        files = store.source_files(run_id)["files"]
        if dataset.strip():
            match = [f for f in files if f["dataset"] == dataset.strip()]
            if not match:
                return {"error": f"Unknown dataset '{dataset}'. Known: {', '.join(f['dataset'] for f in files)}"}
            return match[0]
        summary = []
        for f in files:
            item = {k: f.get(k) for k in ("dataset", "manifest_status", "registered_in_manifest_for_batch", "rows_excluding_header",
                                          "rows_with_wrong_column_count", "ends_with_newline", "duplicate_primary_keys")}
            for key in ("added_columns", "missing_columns", "values_not_matching_schema_type", "manifest_error"):
                if f.get(key):
                    item[key] = f[key]
            if f.get("rows_with_wrong_column_count"):
                item["first_bad_row_numbers"] = f.get("first_bad_row_numbers")
                item["last_row_preview"] = _short(f.get("last_row_preview"), 120)
            summary.append(item)
        return {"batch_date": store.source_files(run_id)["batch_date"], "files": summary}

    return _safe(fetch)


@tool
def get_validation_report(run_id: str) -> str:
    """Get the file and record validation results for the run's batch date: per-dataset validation
    summary and the data quality rules that failed. Use it for data quality questions and to see
    whether validation passed before a later stage failed."""

    def fetch() -> dict:
        report = store.validation(run_id)
        return {**report, "rule_failures": report.get("rule_failures", [])[:12]}

    return _safe(fetch)


@tool
def get_gold_quality(run_id: str) -> str:
    """Get the Gold data quality checks (reconciliation and integrity) for the run's batch date:
    how many ran, which failed. Empty when the run stopped before the Gold quality stage."""

    return _safe(lambda: store.gold_quality(run_id))


@tool
def get_layer_outputs(run_id: str) -> str:
    """Show which Medallion layers (validated, bronze, cdc, silver, silver_dimensions (SCD2),
    enriched_facts, gold, analytics) have output for the run's batch date, what each pipeline stage
    reads and writes, and the safe stage to rerun from. Use it for missing-input errors (for example
    FileNotFoundError on a layer path) and before recommending a rerun."""

    def fetch() -> dict:
        stage = store.restart_stage(run_id)
        return {
            "outputs_present": store.layer_status(run_id).get("outputs_present"),
            "stages": [{"module": s["module"], "reads": s["reads"], "writes": s["writes"]} for s in store.pipeline_stages()],
            "safe_restart_stage": stage["module"],
            "rerun_command": store.rerun_command(run_id),
        }

    return _safe(fetch)


LAKEHOUSE_TOOLS = [
    get_lakehouse_run,
    get_stage_log,
    get_recent_lakehouse_runs,
    check_source_files,
    get_validation_report,
    get_gold_quality,
    get_layer_outputs,
    search_knowledge,
]
LAKEHOUSE_TOOL_NAMES = [t.name for t in LAKEHOUSE_TOOLS]
