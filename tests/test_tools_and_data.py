import json

import pytest

from incident_agent import data_store, tools
from incident_agent.knowledge import KnowledgeBase, load_sections, tokenize
from incident_agent.schemas import CATEGORIES, coerce_report


def test_twelve_open_failures_with_distinct_pipelines_or_days():
    failures = data_store.failed_runs()
    assert len(failures) == 12
    assert all(f["status"] == "Failed" for f in failures)
    # Product master failures that succeeded on rerun earlier are history, not open failures.
    assert "run-1014-ingest-products-a" not in {f["run_id"] for f in failures}


def test_referenced_names_exist_in_the_data():
    for run in data_store.all_runs():
        for name in run["linked_services"]:
            data_store.linked_service(name)
        if "databricks_job" in run and run["status"] == "Failed":
            data_store.job_metrics(run["databricks_job"])
        if "upstream_run_id" in run:
            assert data_store.get_run(run["upstream_run_id"])["status"] == "Failed"


def test_tool_outputs_are_compact_json():
    outputs = [
        tools.get_pipeline_run.invoke({"run_id": "run-1019-ingest-stores"}),
        tools.get_recent_runs.invoke({"pipeline_name": "pl_ingest_sales"}),
        tools.check_linked_service.invoke({"name": "ls_snowflake_reporting"}),
        tools.compare_source_schema.invoke({"dataset": "stores"}),
        tools.get_job_metrics.invoke({"job_name": "nb_silver_sales_join"}),
        tools.check_landing_zone.invoke({"path": "landing/sales/"}),
        tools.search_knowledge.invoke({"query": "resource monitor quota"}),
    ]
    for output in outputs:
        assert len(output) <= 1800
        json.loads(output.removesuffix("...(truncated)")) if not output.endswith("(truncated)") else None
    assert json.loads(outputs[3])["added_columns"] == ["region_code"]


def test_duplicate_file_is_visible_through_md5():
    history = json.loads(tools.get_recent_runs.invoke({"pipeline_name": "pl_ingest_sales", "limit": 2}))
    assert history[0]["file_md5"] == history[1]["file_md5"]
    assert history[0]["source_file"] != history[1]["source_file"]


def test_unknown_names_return_helpful_errors():
    assert "Known pipelines" in tools.get_recent_runs.invoke({"pipeline_name": "pl_nope"})
    assert "Known:" in tools.check_linked_service.invoke({"name": "ls_nope"})


@pytest.mark.parametrize(
    "query,expected_document",
    [
        ("AADSTS7000222 client secret keys expired", "authentication_and_access.md"),
        ("AuthorizationPermissionMismatch role removed", "authentication_and_access.md"),
        ("DelimitedTextColumnCountMismatch new column", "schema_changes.md"),
        ("ExecutorLostFailure Java heap space data skew", "databricks_performance.md"),
        ("UserErrorFileNotFound file arrived late", "source_files_and_landing.md"),
        ("resource monitor exceeded its quota warehouse suspended", "snowflake_operations.md"),
        ("DIAHostService self-hosted integration runtime offline", "integration_runtime.md"),
        ("null rate mandatory column email", "data_quality.md"),
    ],
)
def test_keyword_search_finds_the_right_runbook(query, expected_document):
    kb = KnowledgeBase(embedder=None)
    top = kb.search(query, source="runbooks", k=1)[0][0]
    assert top.document == expected_document


def test_hybrid_search_merges_vector_results():
    import numpy as np

    sections = load_sections()
    target = next(i for i, s in enumerate(sections) if s.document == "integration_runtime.md")

    def embedder(texts, task_type):
        if task_type == "RETRIEVAL_QUERY":
            vector = np.zeros(len(sections), dtype=np.float32)
            vector[target] = 1.0
            return vector[None, :]
        return np.eye(len(sections), dtype=np.float32)

    kb = KnowledgeBase(sections=sections, embedder=embedder)
    assert kb.mode.startswith("hybrid")
    assert kb.search("zzqx vvwq kkpl", k=1)[0][0].document == "integration_runtime.md"


def test_tokenizer_keeps_error_codes():
    assert "aadsts7000222" in tokenize("AADSTS7000222: keys are expired")
    assert "the" not in tokenize("the secret")


def test_report_coercion_handles_messy_output():
    report = coerce_report({"category": "Schema Drift", "recommended_action": "Fix-Then-Rerun", "evidence": "one fact",
                            "confidence": "HIGH", "root_cause": "New column"})
    assert report.category == "schema_drift" and report.recommended_action == "fix_then_rerun"
    assert report.evidence == ["one fact"] and report.confidence == "high"
    assert coerce_report({"category": "aliens"}).category == "unknown"
    assert "unknown" in CATEGORIES
