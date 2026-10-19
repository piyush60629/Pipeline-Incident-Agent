# Runbook: Retail Analytics Lakehouse (PySpark Medallion pipeline)

## How the pipeline runs
- `python run_pipeline.py --batch-date YYYY-MM-DD` runs eight stages in order: `src.incremental` (manifest check, file and record validation, Bronze load), `src.incremental.run_cdc`, `src.incremental_silver`, `src.scd2`, `src.fact_enrichment`, `src.gold`, `src.gold_quality`, `src.analytics`.
- Every layer is written under `data/<layer>/batch_date=<date>/`. Each stage reads the layers written by earlier stages for the SAME batch date.
- `--start-from <module>` skips all earlier stages. Use it only when those stages already succeeded for that batch date.
- Each run writes `monitoring/runs/<run_id>/run.json` (stage statuses, exit codes, error and log tail) and snapshots of the source files, validation reports, Gold quality checks and layer outputs.

## MALFORMED_CSV_RECORD in Bronze ingestion (column count)
Symptoms: `src.incremental` fails with `[MALFORMED_CSV_RECORD] Malformed records are detected in record parsing` and `PipelineExecutionError: Bronze ingestion failed`.
Bronze reads CSVs with an explicit schema in FAILFAST mode, so any row whose column count differs from the schema stops the load.
Diagnosis:
1. Check the source files for the batch: compare received columns with the Bronze schema (added or missing columns) and look for rows with the wrong column count.
2. If the header has an extra or missing column on every row, the source changed its layout: this is schema drift.
3. If only the last row is short and the file does not end with a newline, or the row count is far below the usual volume, the file was cut off during transfer: this is a corrupt (truncated) file, not schema drift.
Fix (schema drift): agree the change with the source owner, add the column to `src/ingestion/schemas.py` (or drop it in the reader with column selection by name), redeploy, then rerun the batch from `src.incremental`.
Fix (truncated file): ask the sender to re-deliver the complete file, verify the row count and that the last row is complete, then rerun from `src.incremental`. Do not load a partial file.

## NumberFormatException or cast errors in Bronze ingestion
Symptoms: `java.lang.NumberFormatException: For input string: "N/A"` (or similar) during the Bronze load.
Cause: a numeric column (for example `price` in products, `unit_price` in order_items, `payment_amount` in payments) contains text placeholders such as `N/A`, `TBD` or empty markers that cannot be cast to the schema type.
Diagnosis: check the source file for values that do not match the schema's numeric types; note the column, how many rows and example values.
Fix: this is a data quality problem in the source extract. Ask the source owner to send numbers or empty values (nulls) instead of text placeholders, or add an explicit rule that maps placeholders to null and quarantines those rows. Then rerun from `src.incremental`.
Prevention: add a record-level validation rule that flags non-numeric values before the FAILFAST Bronze read.

## Duplicate primary keys in a source file
Symptoms: `ValueError: <dataset> contains N duplicate primary keys` during `src.incremental`.
Cause: the source extract contains the same primary key more than once, usually because records were appended twice by the export job.
Diagnosis: check the duplicate primary key count of the source file and whether the row count grew compared with the previous batch.
Fix: ask the sender to re-deliver a de-duplicated file (or de-duplicate on the primary key keeping the latest record if the duplicates are identical), then rerun from `src.incremental`. Never load duplicates into Bronze: CDC would treat them as updates.

## FileNotFoundError for a layer path (missing input)
Symptoms: a later stage (for example `src.gold`) fails with `FileNotFoundError: SCD2 dimension not found: data/silver_dimensions/batch_date=<date>/...` or a missing Silver or enriched facts path.
Cause: the run was started with `--start-from` a later stage, but the earlier stages never ran for this batch date, so their outputs do not exist. Check the run's `start_from` and which layers have output for the batch date.
Fix: nothing in the data needs fixing. Rerun the batch from the first stage whose output is missing (often `src.incremental`, which is a full run without `--start-from`).
Prevention: make `run_pipeline.py` check that the input layers exist before starting from a later stage.

## Rerun policy for the lakehouse
- A rerun must start at the failed stage or at the first stage whose output is missing for the batch date, whichever is earlier.
- Rerun without changes only when the cause is resolved (for example the wrong `--start-from` was used). If a source file is wrong, fix or re-deliver it first.
- Stages overwrite their batch_date partition, so rerunning a batch is safe.

## Known gap: a source file that is not re-delivered
If a daily file is not sent, the manifest marks it unchanged and the pipeline can SUCCEED using the previous day's data. Watch for datasets with `delivered_for_this_batch = false` in the source file check.
