# Runbook: Missing, Late and Duplicate Source Files

## File not found (UserErrorFileNotFound)
1. Check whether the file has arrived in the landing zone since the pipeline started, and compare its arrival time with the agreed arrival SLA.
2. If the file arrived late and is complete (size similar to previous days), simply rerun the pipeline. No code change is needed.
3. If the file has still not arrived, contact the source team and keep the pipeline paused; do not load partial data.
4. Late arrivals before 05:30 IST do not affect the 06:00 IST reporting deadline if rerun promptly.

## Duplicate file delivery
1. Compare the MD5 hash of the new file with files already loaded. The same hash under a different name means the same content was resent.
2. The `dq_unique_transaction_id` check stops the batch before Silver, so Silver and Gold are not affected.
3. Move the duplicate file to the `rejected/` folder and delete the Bronze rows for that batch id.
4. Ask the source team to send the correct file for the missing business date, then rerun.
5. Prevention: reject files whose MD5 matches an already loaded file, not only files with the same name.
