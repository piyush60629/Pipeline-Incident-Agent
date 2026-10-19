# Runbook: Data Quality Check Failures

## Severity
- Critical checks stop the batch before the next layer (Silver or Gold is not refreshed).
- Warning checks load the data, write failed rows to the error table, and alert the data quality channel.

## Null rate above threshold on a mandatory column
1. Check whether the source system changed the column (for example NOT NULL to NULLABLE) and when.
2. Mandatory columns have a 0% null threshold. Do not relax a threshold without agreement from the data owner.
3. Contact the source data owner to confirm whether nulls are now valid (a business change) or a defect.
4. If nulls are valid, the data owner approves a rule change (for example a default value or a separate rule for the new customer type); then rerun.
5. If it is a defect, the source team fixes the data and resends; then rerun.

## Duplicate keys
See the duplicate file section of the source files runbook.
