"""Read-only access to the synthetic platform data (the agent's "monitoring systems").

In a real deployment these functions would call the ADF REST API, Azure Monitor / Log
Analytics, the Databricks Jobs API and storage listings. Here they read JSON files from
data/, which keeps the demo free, offline and repeatable.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import DATA_DIR


class NotFound(LookupError):
    """Raised when an id or name does not exist; tools turn it into a helpful message."""


@lru_cache(maxsize=None)
def _load(name: str, data_dir: str = str(DATA_DIR)) -> Any:
    return json.loads((Path(data_dir) / name).read_text(encoding="utf-8"))


def all_runs() -> list[dict]:
    return _load("pipeline_runs.json")["runs"]


def failed_runs(latest_only: bool = True) -> list[dict]:
    """Failed runs, newest first. latest_only skips failures that were already fixed by a later success."""

    runs = sorted(all_runs(), key=lambda r: r["start"], reverse=True)
    failures = [r for r in runs if r["status"] == "Failed"]
    if not latest_only:
        return failures
    latest_status: dict[str, dict] = {}
    for run in runs:
        latest_status.setdefault(run["pipeline"], run)
    open_failures = []
    for run in failures:
        later_success = any(
            r["pipeline"] == run["pipeline"] and r["status"] == "Succeeded" and r["start"] > run["start"] for r in runs
        )
        if not later_success:
            open_failures.append(run)
    return open_failures


def get_run(run_id: str) -> dict:
    for run in all_runs():
        if run["run_id"] == run_id.strip():
            return run
    raise NotFound(f"No pipeline run with id '{run_id}'.")


def recent_runs(pipeline: str, limit: int = 7) -> list[dict]:
    runs = [r for r in all_runs() if r["pipeline"] == pipeline.strip()]
    if not runs:
        known = sorted({r["pipeline"] for r in all_runs()})
        raise NotFound(f"Unknown pipeline '{pipeline}'. Known pipelines: {', '.join(known)}")
    return sorted(runs, key=lambda r: r["start"], reverse=True)[: max(1, min(limit, 20))]


def linked_service(name: str) -> dict:
    services = _load("linked_services.json")
    if name.strip() not in services:
        raise NotFound(f"Unknown linked service '{name}'. Known: {', '.join(sorted(services))}")
    return services[name.strip()]


def source_schema(dataset: str) -> dict:
    schemas = _load("source_schemas.json")
    if dataset.strip() not in schemas:
        raise NotFound(f"No schema registered for dataset '{dataset}'. Known: {', '.join(sorted(schemas))}")
    return schemas[dataset.strip()]


def job_metrics(job_name: str) -> dict:
    metrics = _load("job_metrics.json")
    if job_name.strip() not in metrics:
        raise NotFound(f"No metrics for Databricks job '{job_name}'. Known: {', '.join(sorted(metrics))}")
    return metrics[job_name.strip()]


def landing_files(path_prefix: str) -> dict:
    landing = _load("landing_zone.json")
    prefix = path_prefix.strip()
    folder = prefix if prefix.endswith("/") else prefix.rsplit("/", 1)[0] + "/"
    files = [f for f in landing["files"] if f["path"].startswith(prefix) or f["path"].startswith(folder)]
    return {
        "query": prefix,
        "exact_match": next((f for f in files if f["path"] == prefix), None),
        "files_in_folder": sorted(files, key=lambda f: f["arrived"], reverse=True)[:6],
        "arrival_sla_ist": landing["arrival_slas_ist"].get(folder),
    }


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
        "status": "Queued",
        "approved_by": approved_by,
        "reason": reason[:300],
        "queued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Simulated: in production this would call the ADF createRun REST API.",
    }
    with _rerun_lock:
        _reruns.append(record)
    return record


def rerun_log() -> list[dict]:
    with _rerun_lock:
        return list(_reruns)
