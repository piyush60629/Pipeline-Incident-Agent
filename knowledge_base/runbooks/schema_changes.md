# Runbook: Source Schema Changes (Schema Drift)

## Symptoms
- `DelimitedTextColumnCountMismatch`, `Column not found`, or type conversion errors in copy activities.
- A source file with more or fewer columns than the dataset definition.

## Diagnosis
1. Compare the latest received file's header with the dataset's expected columns.
2. Check the source system's release notes for the change date.
3. Decide with the data owner whether the new column is needed downstream.

## Fix
1. Update the dataset schema and the copy activity column mapping (add the new column, or map by name and ignore it).
2. If the column is needed downstream, add it to Bronze and Silver tables with a default for historical rows.
3. Reprocess the rejected file from the quarantine/landing folder by rerunning the pipeline after the fix is deployed.
4. Prevention: map columns by name instead of position, and ask source teams to announce schema changes in the release checklist.
