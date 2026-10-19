"""Generate the synthetic pipeline data used by the agent's tools.

Everything here is invented for a portfolio demo: a retail data platform on Azure Data
Factory, ADLS Gen2, Databricks and Snowflake, with 12 failed runs that each have a
different root cause. Run it again to rebuild the JSON files in data/:

    python scripts/generate_sample_data.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
IST = "+05:30"
TODAY = datetime(2026, 10, 19)


def ts(day: datetime, hhmm: str) -> str:
    hour, minute = map(int, hhmm.split(":"))
    return day.replace(hour=hour, minute=minute).strftime("%Y-%m-%dT%H:%M:00") + IST


def end_ts(day: datetime, hhmm: str, minutes: float) -> str:
    hour, minute = map(int, hhmm.split(":"))
    start = day.replace(hour=hour, minute=minute)
    return (start + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S") + IST


PIPELINES = {
    "pl_ingest_customers": {"start": "02:15", "minutes": 6, "activity": "Copy_Customers_To_Bronze", "type": "Copy",
                            "linked_services": ["ls_adls_crm"], "dataset": "customers", "rows": 182_000},
    "pl_ingest_sales": {"start": "02:15", "minutes": 9, "activity": "Copy_Sales_To_Bronze", "type": "Copy",
                        "linked_services": ["ls_adls_retail"], "dataset": "sales", "rows": 1_240_000},
    "pl_ingest_stores": {"start": "02:20", "minutes": 2, "activity": "Copy_Stores_To_Bronze", "type": "Copy",
                         "linked_services": ["ls_adls_retail"], "dataset": "stores", "rows": 412},
    "pl_ingest_inventory": {"start": "02:15", "minutes": 7, "activity": "Copy_Inventory_To_Bronze", "type": "Copy",
                            "linked_services": ["ls_adls_retail"], "dataset": "inventory", "rows": 860_000},
    "pl_ingest_products": {"start": "02:25", "minutes": 4, "activity": "Copy_Products_From_SQL", "type": "Copy",
                           "linked_services": ["ls_sql_product_master", "ls_adls_retail"], "dataset": "products", "rows": 48_500},
    "pl_ingest_onprem_branches": {"start": "02:30", "minutes": 5, "activity": "Copy_Branch_Master", "type": "Copy",
                                  "linked_services": ["ls_sqlserver_onprem_branches"], "dataset": "branches", "rows": 2_150},
    "pl_transform_silver_sales": {"start": "03:00", "minutes": 24, "activity": "Run_nb_silver_sales_join",
                                  "type": "DatabricksNotebook", "linked_services": ["ls_databricks_retail"],
                                  "databricks_job": "nb_silver_sales_join", "rows": 1_240_000},
    "pl_transform_silver_customers": {"start": "03:00", "minutes": 8, "activity": "Run_nb_silver_customers",
                                      "type": "DatabricksNotebook", "linked_services": ["ls_databricks_retail"],
                                      "databricks_job": "nb_silver_customers", "dataset": "customers", "rows": 182_000},
    "pl_transform_gold_sales": {"start": "03:45", "minutes": 40, "activity": "Run_nb_gold_sales_fact",
                                "type": "DatabricksNotebook", "linked_services": ["ls_databricks_retail"],
                                "databricks_job": "nb_gold_sales_fact", "rows": 9_100_000},
    "pl_load_gold_finance": {"start": "04:40", "minutes": 11, "activity": "Write_Fact_Finance", "type": "Copy",
                             "linked_services": ["ls_adls_finance"], "rows": 640_000},
    "pl_load_snowflake_reporting": {"start": "05:00", "minutes": 14, "activity": "Load_Reporting_Marts", "type": "Copy",
                                    "linked_services": ["ls_snowflake_reporting"], "rows": 2_300_000},
}


def success_run(name: str, day: datetime, minutes: float | None = None, rows: int | None = None, **extra) -> dict:
    spec = PIPELINES[name]
    minutes = spec["minutes"] if minutes is None else minutes
    run = {
        "run_id": f"run-{day:%m%d}-{name.removeprefix('pl_').replace('_', '-')}",
        "pipeline": name,
        "activity": spec["activity"],
        "activity_type": spec["type"],
        "status": "Succeeded",
        "start": ts(day, spec["start"]),
        "end": end_ts(day, spec["start"], minutes),
        "duration_min": round(minutes, 1),
        "rows_read": rows if rows is not None else spec["rows"],
        "triggered_by": "schedule",
        "linked_services": spec["linked_services"],
    }
    for key in ("dataset", "databricks_job"):
        if key in spec:
            run[key] = spec[key]
    run.update(extra)
    return run


def failed_run(name: str, day: datetime, minutes: float, error_code: str, error_message: str, **extra) -> dict:
    run = success_run(name, day, minutes=minutes, rows=0, **extra)
    run.update(status="Failed", error_code=error_code, error_message=error_message)
    return run


def build_runs() -> list[dict]:
    runs: list[dict] = []
    history_days = [TODAY - timedelta(days=d) for d in range(7, 0, -1)]  # 12th .. 18th

    # Normal history for every pipeline (the special cases below override some days).
    for name in PIPELINES:
        for day in history_days:
            runs.append(success_run(name, day))

    def replace(run: dict) -> None:
        runs[:] = [r for r in runs if r["run_id"] != run["run_id"]]
        runs.append(run)

    # --- Gold sales fact: duration creeping up as data grows, then a timeout on the 18th.
    durations = [38, 41, 44, 47, 51, 55]
    rows = [9_100_000, 9_600_000, 10_100_000, 10_700_000, 11_300_000, 11_900_000]
    for day, minutes, row_count in zip(history_days[:6], durations, rows):
        replace(success_run("pl_transform_gold_sales", day, minutes=minutes, rows=row_count))
    replace(failed_run(
        "pl_transform_gold_sales", TODAY - timedelta(days=1), 60,
        "ActivityTimedOut",
        "Operation on target Run_nb_gold_sales_fact failed: Activity timed out after 01:00:00. "
        "The activity 'timeout' policy is set to 01:00:00.",
        timeout_policy="01:00:00",
    ))

    # --- Sales ingestion: yesterday's file is delivered again today under a new name.
    replace(success_run("pl_ingest_sales", TODAY - timedelta(days=1),
                        source_file="landing/sales/sales_20261018.csv", file_md5="9f2c41ab77d0"))
    for day in history_days[:-1]:
        replace(success_run("pl_ingest_sales", day,
                            source_file=f"landing/sales/sales_{day:%Y%m%d}.csv",
                            file_md5=f"{(day.day * 7919) % 0xFFFFFF:06x}a1c0"))

    # --- Product master: transient connection resets that succeeded on rerun before.
    for day_offset in (5, 2):
        day = TODAY - timedelta(days=day_offset)
        first = failed_run(
            "pl_ingest_products", day, 1.2,
            "SqlFailedToConnect",
            "ErrorCode=SqlFailedToConnect, A connection was successfully established with the server, but then an "
            "error occurred during the pre-login handshake. (provider: TCP Provider, error: 35 - An internal "
            "exception was caught) An existing connection was forcibly closed by the remote host.",
        )
        first["run_id"] += "-a"
        retry = success_run("pl_ingest_products", day, triggered_by="manual rerun")
        retry["run_id"] += "-b"
        retry["start"] = ts(day, "02:55")
        retry["end"] = end_ts(day, "02:55", 4)
        runs[:] = [r for r in runs if r["run_id"] != f"run-{day:%m%d}-ingest-products"]
        runs.extend([first, retry])

    # --- Today's failures (the 12th case is the gold sales timeout on the 18th, above) ---
    day = TODAY
    failures = [
        failed_run(
            "pl_ingest_customers", day, 0.8, "AdlsGen2OperationFailed",
            "ErrorCode=AdlsGen2OperationFailed, Message=ADLS Gen2 operation failed for: Operation returned an invalid "
            "status code 'Unauthorized'. Account: 'stcrmlanding'. FileSystem: 'crm'. ErrorCode: 'InvalidAuthenticationInfo'. "
            "Message: 'Server failed to authenticate the request.'",
        ),
        failed_run(
            "pl_load_gold_finance", day, 1.1, "AdlsGen2OperationFailed",
            "ErrorCode=AdlsGen2OperationFailed, Message=ADLS Gen2 operation failed for: Operation returned an invalid "
            "status code 'Forbidden'. Account: 'stretailgold'. FileSystem: 'gold'. Path: 'finance/fact_finance/'. "
            "ErrorCode: 'AuthorizationPermissionMismatch'. Message: 'This request is not authorized to perform this "
            "operation using this permission.'",
        ),
        failed_run(
            "pl_ingest_stores", day, 0.5, "DelimitedTextColumnCountMismatch",
            "ErrorCode=DelimitedTextColumnCountMismatch, Message=Column count mismatch in file "
            "'landing/stores/stores_20261019.csv': found 9 columns but the dataset schema defines 8.",
            source_file="landing/stores/stores_20261019.csv",
        ),
        failed_run(
            "pl_transform_silver_sales", day, 31, "DatabricksJobFailed",
            "Operation on target Run_nb_silver_sales_join failed: Job run failed. org.apache.spark.SparkException: "
            "Job aborted due to stage failure: Task 187 in stage 14.0 failed 4 times, most recent failure: "
            "ExecutorLostFailure (executor 7 exited caused by one of the running tasks) Reason: Container killed "
            "for exceeding memory limits. java.lang.OutOfMemoryError: Java heap space",
        ),
        failed_run(
            "pl_transform_gold_sales", day, 0.2, "UpstreamDependencyFailed",
            "Operation on target Wait_For_Silver_Sales failed: Dependency check failed. Upstream pipeline "
            "'pl_transform_silver_sales' run 'run-1019-transform-silver-sales' finished with status 'Failed'.",
            activity="Wait_For_Silver_Sales", activity_type="ExecutePipeline",
            upstream_run_id="run-1019-transform-silver-sales",
        ),
        failed_run(
            "pl_ingest_sales", day, 9.4, "DataQualityCheckFailed",
            "Data quality check 'dq_unique_transaction_id' (severity: Critical) failed on bronze.sales: "
            "1,186,204 transaction_id values already exist in bronze.sales for the same business date. "
            "Batch stopped before Silver.",
            source_file="landing/sales/sales_20261018_v2.csv", file_md5="9f2c41ab77d0",
        ),
        failed_run(
            "pl_ingest_inventory", day, 0.3, "UserErrorFileNotFound",
            "ErrorCode=UserErrorFileNotFound, Message=Path not found: 'landing/inventory/inventory_20261019.csv'. "
            "The specified path does not exist.",
            source_file="landing/inventory/inventory_20261019.csv",
        ),
        failed_run(
            "pl_transform_silver_customers", day, 6.5, "DataQualityCheckFailed",
            "Data quality check 'dq_null_rate_email' (severity: Critical) failed on silver.customers: "
            "null rate for mandatory column 'email' is 7.4% (threshold 0%). Batch stopped before Gold.",
        ),
        failed_run(
            "pl_load_snowflake_reporting", day, 0.4, "SnowflakeExecutionFailed",
            "ErrorCode=SnowflakeExecutionFailed, Message=Failed to execute the query command during write operation. "
            "SQL execution error: Warehouse 'WH_REPORTING' cannot be resumed because resource monitor "
            "'RM_MONTHLY_REPORTING' has exceeded its quota.",
        ),
        failed_run(
            "pl_ingest_products", day, 1.3, "SqlFailedToConnect",
            "ErrorCode=SqlFailedToConnect, A connection was successfully established with the server, but then an "
            "error occurred during the pre-login handshake. (provider: TCP Provider, error: 35 - An internal "
            "exception was caught) An existing connection was forcibly closed by the remote host.",
        ),
        failed_run(
            "pl_ingest_onprem_branches", day, 0.2, "UserErrorSelfHostedIrOffline",
            "ErrorCode=UserErrorSelfHostedIrOffline, Message=The Self-hosted Integration Runtime 'shir-onprem-01' "
            "used by linked service 'ls_sqlserver_onprem_branches' is offline. Please check the integration runtime status.",
        ),
    ]
    runs.extend(failures)
    return sorted(runs, key=lambda r: (r["start"], r["pipeline"]))


LINKED_SERVICES = {
    "ls_adls_crm": {
        "type": "AzureBlobFS (ADLS Gen2)", "account": "stcrmlanding", "auth": "ServicePrincipal",
        "service_principal": "sp-crm-ingest", "secret_reference": "kv-retail-prod/sp-crm-ingest-secret",
        "secret_expires": "2026-10-18T23:59:00+05:30", "secret_last_rotated": "2025-10-19",
        "test_connection": "Failed: AADSTS7000222: The provided client secret keys for app 'sp-crm-ingest' are expired.",
        "recent_changes": [],
    },
    "ls_adls_retail": {
        "type": "AzureBlobFS (ADLS Gen2)", "account": "stretaillake", "auth": "ManagedIdentity",
        "identity": "adf-retail-prod", "test_connection": "Succeeded", "recent_changes": [],
    },
    "ls_adls_finance": {
        "type": "AzureBlobFS (ADLS Gen2)", "account": "stretailgold", "auth": "ManagedIdentity",
        "identity": "adf-retail-prod", "test_connection": "Failed: 403 AuthorizationPermissionMismatch on 'gold/finance'",
        "recent_changes": [
            {"date": "2026-10-18", "by": "cloud-governance",
             "change": "IAM cleanup removed role 'Storage Blob Data Contributor' for identity 'adf-retail-prod' "
                       "on container 'gold' (ticket GOV-5521)."},
        ],
    },
    "ls_sql_product_master": {
        "type": "AzureSqlDatabase", "server": "sql-product-master.database.windows.net", "auth": "ManagedIdentity",
        "test_connection": "Succeeded (checked 2026-10-19 06:10)",
        "notes": "Network team reported short TCP resets on the ExpressRoute gateway between 02:20 and 02:35 "
                 "on 2026-10-14, 2026-10-17 and 2026-10-19 (maintenance window).",
        "recent_changes": [],
    },
    "ls_sqlserver_onprem_branches": {
        "type": "SqlServer (on-premises)", "integration_runtime": "shir-onprem-01", "auth": "SqlAuthentication",
        "test_connection": "Failed: integration runtime offline",
        "integration_runtime_status": {
            "name": "shir-onprem-01", "state": "Offline", "node": "vm-shir-01",
            "last_heartbeat": "2026-10-19T00:47:00+05:30",
            "node_events": ["2026-10-19 00:45 vm-shir-01 restarted for Windows patching (KB5049981)",
                            "Service 'DIAHostService' startup type is Manual and did not start after reboot"],
        },
        "recent_changes": [],
    },
    "ls_snowflake_reporting": {
        "type": "Snowflake", "account": "retail-analytics.central-india.azure", "warehouse": "WH_REPORTING",
        "auth": "KeyPair", "test_connection": "Succeeded (connection only; warehouse is suspended)",
        "warehouse_state": "SUSPENDED",
        "resource_monitor": {"name": "RM_MONTHLY_REPORTING", "credit_quota": 500, "credits_used": 500,
                             "used_percent": 100, "notify_at_percent": 80, "suspend_at_percent": 100,
                             "notified": "2026-10-12 to platform-alerts@ (no action recorded)",
                             "resets_on": "2026-11-01"},
        "recent_changes": [],
    },
    "ls_databricks_retail": {
        "type": "AzureDatabricks", "workspace": "adb-retail-prod", "auth": "ManagedIdentity",
        "test_connection": "Succeeded", "recent_changes": [],
    },
}

SOURCE_SCHEMAS = {
    "stores": {
        "expected_columns": ["store_id", "store_name", "city", "state", "store_type", "opened_on", "manager_id", "is_active"],
        "latest_file": "landing/stores/stores_20261019.csv",
        "received_columns": ["store_id", "store_name", "city", "state", "store_type", "region_code", "opened_on",
                             "manager_id", "is_active"],
        "source_release_notes": "POS master data v5.2 released 2026-10-18: new column 'region_code' added after "
                                "'store_type'. No advance notice to the data platform team.",
    },
    "customers": {
        "expected_columns": ["customer_id", "full_name", "email", "phone", "city", "signup_date", "loyalty_tier"],
        "latest_file": "landing/crm/customers_20261019.parquet",
        "received_columns": ["customer_id", "full_name", "email", "phone", "city", "signup_date", "loyalty_tier"],
        "column_changes": [{"column": "email", "change": "NOT NULL -> NULLABLE",
                            "source": "CRM release 2026.10 on 2026-10-18 (guest checkout customers have no email)"}],
        "null_rates_today": {"email": "7.4%", "phone": "1.1%"},
    },
    "sales": {
        "expected_columns": ["transaction_id", "line_number", "store_id", "product_id", "quantity", "gross_amount",
                             "discount_amount", "net_amount", "transaction_ts"],
        "latest_file": "landing/sales/sales_20261018_v2.csv",
        "received_columns": ["transaction_id", "line_number", "store_id", "product_id", "quantity", "gross_amount",
                             "discount_amount", "net_amount", "transaction_ts"],
    },
    "inventory": {
        "expected_columns": ["snapshot_date", "store_id", "product_id", "on_hand_qty", "on_order_qty"],
        "latest_file": "landing/inventory/inventory_20261019.csv",
        "received_columns": ["snapshot_date", "store_id", "product_id", "on_hand_qty", "on_order_qty"],
    },
}

JOB_METRICS = {
    "nb_gold_sales_fact": {
        "cluster": "job-cluster Standard_DS4_v2 x 6 workers", "activity_timeout": "01:00:00",
        "recent_runs": [
            {"date": "2026-10-12", "duration_min": 38, "input_rows": 9_100_000},
            {"date": "2026-10-13", "duration_min": 41, "input_rows": 9_600_000},
            {"date": "2026-10-14", "duration_min": 44, "input_rows": 10_100_000},
            {"date": "2026-10-15", "duration_min": 47, "input_rows": 10_700_000},
            {"date": "2026-10-16", "duration_min": 51, "input_rows": 11_300_000},
            {"date": "2026-10-17", "duration_min": 55, "input_rows": 11_900_000},
            {"date": "2026-10-18", "duration_min": 60, "input_rows": 12_400_000, "status": "TimedOut"},
        ],
        "notes": "Festive-season sales volume up ~36% in one week. gold.fact_sales is rewritten in full every run "
                 "(not partitioned by transaction_date, no incremental MERGE).",
    },
    "nb_silver_sales_join": {
        "cluster": "job-cluster Standard_DS3_v2 x 4 workers (14 GB each)",
        "last_run": {"date": "2026-10-19", "status": "Failed", "error": "ExecutorLostFailure / OutOfMemoryError",
                     "failed_stage": "stage 14 (join sales x stores on store_id)",
                     "task_input_median_mb": 118, "task_input_max_mb": 9_600,
                     "skewed_key": {"column": "store_id", "value": "S-0001 (online store)", "share_of_rows": "41%"}},
        "spark_conf": {"spark.sql.adaptive.enabled": "true", "spark.sql.adaptive.skewJoin.enabled": "false",
                       "spark.sql.autoBroadcastJoinThreshold": "-1"},
        "notes": "Online-store sales share jumped from 12% to 41% after the festive sale launch on 2026-10-18.",
    },
    "nb_silver_customers": {
        "cluster": "job-cluster Standard_DS3_v2 x 2 workers",
        "last_run": {"date": "2026-10-19", "status": "Failed", "error": "DataQualityCheckFailed dq_null_rate_email",
                     "duration_min": 6.5},
        "notes": "No performance issues.",
    },
}

LANDING_ZONE = [
    {"path": "landing/sales/sales_20261017.csv", "arrived": "2026-10-17T01:31:00+05:30", "size_mb": 212, "md5": "7c1e09d2b4a1"},
    {"path": "landing/sales/sales_20261018.csv", "arrived": "2026-10-18T01:32:00+05:30", "size_mb": 219, "md5": "9f2c41ab77d0"},
    {"path": "landing/sales/sales_20261018_v2.csv", "arrived": "2026-10-19T01:35:00+05:30", "size_mb": 219, "md5": "9f2c41ab77d0",
     "sender_note": "Resent by POS export job after a retry on the source side."},
    {"path": "landing/inventory/inventory_20261017.csv", "arrived": "2026-10-17T01:42:00+05:30", "size_mb": 47, "md5": "a51d77c03e19"},
    {"path": "landing/inventory/inventory_20261018.csv", "arrived": "2026-10-18T01:39:00+05:30", "size_mb": 47, "md5": "c2f8e1a90b44"},
    {"path": "landing/inventory/inventory_20261019.csv", "arrived": "2026-10-19T03:05:00+05:30", "size_mb": 48, "md5": "e93b0d5f6a12",
     "sender_note": "Warehouse system export delayed by month-end stock count."},
    {"path": "landing/stores/stores_20261019.csv", "arrived": "2026-10-19T01:50:00+05:30", "size_mb": 0.1, "md5": "41aa9c0e7d55"},
    {"path": "landing/crm/customers_20261019.parquet", "arrived": "2026-10-19T01:20:00+05:30", "size_mb": 18, "md5": "0be47c2d91f3"},
]
LANDING_SLAS = {"landing/sales/": "01:45", "landing/inventory/": "02:00", "landing/stores/": "02:00", "landing/crm/": "01:45"}


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    payloads = {
        "pipeline_runs.json": {"generated_for": "2026-10-19", "runs": build_runs()},
        "linked_services.json": LINKED_SERVICES,
        "source_schemas.json": SOURCE_SCHEMAS,
        "job_metrics.json": JOB_METRICS,
        "landing_zone.json": {"files": LANDING_ZONE, "arrival_slas_ist": LANDING_SLAS},
    }
    for name, payload in payloads.items():
        (DATA_DIR / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote data/{name}")


if __name__ == "__main__":
    main()
