"""Read-only access to REAL run logs from the Retail Analytics Lakehouse.

The lakehouse project (https://github.com/piyush60629/Retail-Analytics-Lakehouse) writes one
folder per pipeline run:

    monitoring/
      index.json                      summary of every run
      runs/<run_id>/run.json          status, timings, per-stage exit codes, error and log tail
      runs/<run_id>/source_files.json raw CSVs: manifest status, rows, ragged rows, columns vs schema
      runs/<run_id>/validation.json   validation audit and failed data quality rules
      runs/<run_id>/gold_quality.json Gold data quality checks
      runs/<run_id>/layer_status.json which Medallion layers have output for the batch date

A copy of that folder is kept in lakehouse/monitoring/ (scripts/sync_lakehouse_runs.py refreshes it).
The stage map in lakehouse/pipeline_stages.json describes what each stage reads and writes.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import ROOT
from .data_store import NotFound

LAKEHOUSE_DIR = Path(os.getenv("LAKEHOUSE_DIR", str(ROOT / "lakehouse")))


def monitoring_dir() -> Path:
    return LAKEHOUSE_DIR / "monitoring"


@lru_cache(maxsize=None)
def _json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _run_file(run_id: str, name: str) -> dict:
    run_id = run_id.strip()
    path = monitoring_dir() / "runs" / run_id / f"{name}.json"
    if not path.exists():
        known = ", ".join(r["run_id"] for r in all_runs()[-8:])
        raise NotFound(f"No lakehouse run '{run_id}'. Recent runs: {known}")
    return _json(str(path))


def all_runs() -> list[dict]:
    """Run summaries, oldest first."""

    index = monitoring_dir() / "index.json"
    return _json(str(index))["runs"] if index.exists() else []


def failed_runs() -> list[dict]:
    """Failed runs, newest first, each with its error for display."""

    failures = []
    for summary in reversed(all_runs()):
        if summary["status"] == "FAILED":
            run = get_run(summary["run_id"])
            failures.append({**summary, "error_message": run.get("error_message") or ""})
    return failures


def get_run(run_id: str) -> dict:
    return _run_file(run_id, "run")


def recent_runs(limit: int = 7) -> list[dict]:
    return list(reversed(all_runs()))[: max(1, min(limit, 20))]


def source_files(run_id: str) -> dict:
    return _run_file(run_id, "source_files")


def validation(run_id: str) -> dict:
    return _run_file(run_id, "validation")


def gold_quality(run_id: str) -> dict:
    return _run_file(run_id, "gold_quality")


def layer_status(run_id: str) -> dict:
    return _run_file(run_id, "layer_status")


def pipeline_stages() -> list[dict]:
    return _json(str(LAKEHOUSE_DIR / "pipeline_stages.json"))["stages"]


def restart_stage(run_id: str) -> dict:
    """The safe place to rerun from: the failed stage, or an earlier stage whose output is missing
    for the run's batch date (rerunning later would read inputs that were never written)."""

    present = layer_status(run_id).get("outputs_present", {})
    failed_module = next((s["module"] for s in get_run(run_id)["stages"] if s["status"] == "FAILED"), None)
    for stage in pipeline_stages():
        if stage["module"] == failed_module or not all(present.get(layer) for layer in stage["writes"]):
            return stage
    return pipeline_stages()[-1]


def rerun_command(run_id: str) -> str:
    run, stage = get_run(run_id), restart_stage(run_id)
    first = pipeline_stages()[0]["module"]
    start = "" if stage["module"] == first else f" --start-from {stage['module']}"
    return f"python run_pipeline.py --batch-date {run['batch_date']}{start}"


# --------------------------------------------------------------------------- #
# The one write action: a simulated rerun, only reachable after human approval
# --------------------------------------------------------------------------- #

_rerun_lock = threading.Lock()
_reruns: list[dict] = []


def trigger_rerun(run_id: str, approved_by: str, reason: str = "") -> dict:
    run = get_run(run_id)
    record = {
        "rerun_id": f"{run_id}-rerun-{uuid.uuid4().hex[:6]}",
        "pipeline": run["pipeline"],
        "original_run_id": run_id,
        "command": rerun_command(run_id),
        "status": "Queued",
        "approved_by": approved_by,
        "reason": reason[:300],
        "queued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Simulated: in production this would start the command on the scheduler (for example an Airflow task).",
    }
    with _rerun_lock:
        _reruns.append(record)
    return record


def rerun_log() -> list[dict]:
    with _rerun_lock:
        return list(_reruns)
