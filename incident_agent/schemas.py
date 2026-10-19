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

    category: Category = Field(description="Failure category. Use 'unknown' only if the evidence is insufficient.")
    root_cause: str = Field(description="One or two sentences: what actually caused the failure, based on tool evidence.")
    confidence: Confidence = Field(description="high = confirmed by tool evidence; medium = likely; low = guess.")
    evidence: list[str] = Field(description="2-5 short facts from tool results that support the root cause.")
    fix_steps: list[str] = Field(description="Ordered steps to resolve the failure, following the runbook where one applies.")
    prevention: list[str] = Field(default_factory=list, description="1-3 steps that stop this happening again.")
    recommended_action: Action = Field(
        description="'rerun' = safe to rerun now with no change (transient or already resolved); "
        "'fix_then_rerun' = something must be fixed first; 'escalate' = another team must act "
        "(access, quotas, source data owners); 'no_action' = nothing to do."
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
