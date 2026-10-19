"""The structured diagnosis the agent must submit."""

from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

Category = Literal[
    "authentication",
    "permission",
    "schema_drift",
    "timeout",
    "out_of_memory",
    "upstream_dependency",
    "duplicate_data",
    "source_file_missing",
    "corrupt_source_file",
    "data_quality",
    "resource_quota",
    "transient_network",
    "integration_runtime",
    "unknown",
]
Action = Literal["rerun", "fix_then_rerun", "escalate", "no_action"]
Confidence = Literal["high", "medium", "low"]

CATEGORIES: tuple[str, ...] = get_args(Category)
ACTIONS: tuple[str, ...] = get_args(Action)


class DiagnosisReport(BaseModel):
    """Final diagnosis of a failed pipeline run."""

    category: Category = Field(
        description="Failure category of THIS run. Rules: a run that failed because an upstream pipeline failed is "
        "'upstream_dependency' (even if the upstream failure was out of memory); a failed data quality check is "
        "'data_quality' (even if a source change caused it); 'schema_drift' is only for added, removed or renamed "
        "columns that break the load; 'corrupt_source_file' = a source file that is truncated, cut off mid-row or "
        "otherwise unreadable (a wrong value inside a well-formed file is 'data_quality'); a run started from a later "
        "stage whose inputs were never produced is 'upstream_dependency'; 'permission' = authenticated but not allowed (403); 'authentication' = "
        "credentials rejected or expired; 'unknown' only if the evidence is insufficient."
    )
    root_cause: str = Field(description="One or two sentences: what actually caused the failure, based on tool evidence.")
    confidence: Confidence = Field(description="high = confirmed by tool evidence; medium = likely; low = guess.")
    evidence: list[str] = Field(description="2-5 short facts from tool results that support the root cause.")
    fix_steps: list[str] = Field(description="Ordered steps to resolve the failure, following the runbook where one applies.")
    prevention: list[str] = Field(default_factory=list, description="1-3 steps that stop this happening again.")
    recommended_action: Action = Field(
        description="'rerun' = safe to rerun now with no change (transient error, or the cause is already resolved); "
        "'fix_then_rerun' = the data team can fix it, then rerun; 'escalate' = only another team can fix or approve "
        "it (IAM role assignments, Snowflake quotas / resource monitors, source data owners deciding a rule); "
        "'no_action' = nothing to do."
    )
    sources: list[str] = Field(default_factory=list, description="Runbook / incident citations used.")
    incident_summary: str = Field(description="Three to four sentences for the incident ticket: impact, cause, next step.")


def coerce_report(raw: dict[str, Any] | None) -> DiagnosisReport:
    """Build a report from model output even if some fields are missing or slightly off."""

    raw = dict(raw or {})

    def as_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        return [str(item) for item in value if str(item).strip()]

    category = str(raw.get("category", "unknown")).strip().lower().replace(" ", "_").replace("-", "_")
    action = str(raw.get("recommended_action", "escalate")).strip().lower().replace(" ", "_").replace("-", "_")
    confidence = str(raw.get("confidence", "low")).strip().lower()
    return DiagnosisReport(
        category=category if category in CATEGORIES else "unknown",
        root_cause=str(raw.get("root_cause") or "Root cause not determined.").strip(),
        confidence=confidence if confidence in ("high", "medium", "low") else "low",
        evidence=as_list(raw.get("evidence")),
        fix_steps=as_list(raw.get("fix_steps")),
        prevention=as_list(raw.get("prevention")),
        recommended_action=action if action in ACTIONS else "escalate",
        sources=as_list(raw.get("sources")),
        incident_summary=str(raw.get("incident_summary") or raw.get("root_cause") or "").strip(),
    )


def report_field_guide() -> str:
    """Field-by-field instructions for the report step, generated from the schema above."""

    lines = []
    for name, field in DiagnosisReport.model_fields.items():
        allowed = ""
        if name == "category":
            allowed = f" One of: {', '.join(CATEGORIES)}."
        elif name == "recommended_action":
            allowed = f" One of: {', '.join(ACTIONS)}."
        elif name == "confidence":
            allowed = " One of: high, medium, low."
        kind = "list of strings" if name in ("evidence", "fix_steps", "prevention", "sources") else "string"
        lines.append(f'- "{name}" ({kind}): {field.description}{allowed}')
    return "\n".join(lines)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Return the last JSON object in the text that looks like a report (tolerates fences or prose)."""

    import json
    import re

    decoder, found = json.JSONDecoder(), None
    for match in re.finditer(r"\{", text or ""):
        try:
            candidate, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and ("root_cause" in candidate or "category" in candidate):
            found = candidate
    return found
