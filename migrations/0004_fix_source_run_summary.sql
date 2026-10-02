DROP VIEW source_run_summary;

CREATE VIEW source_run_summary AS
WITH sources(source_name) AS (VALUES('openrouter'),('vast'),('aws_spot'))
SELECT s.source_name AS source,
 (SELECT started_at_utc FROM collection_runs r WHERE r.source=s.source_name ORDER BY started_at_utc DESC LIMIT 1) AS last_attempt,
 (SELECT finished_at_utc FROM collection_runs r WHERE r.source=s.source_name AND status='succeeded' ORDER BY finished_at_utc DESC LIMIT 1) AS last_success,
 (SELECT status FROM collection_runs r WHERE r.source=s.source_name ORDER BY started_at_utc DESC LIMIT 1) AS last_status,
 (SELECT COUNT(*) FROM collection_runs r WHERE r.source=s.source_name AND status IN ('failed','quarantined')
   AND started_at_utc > COALESCE((SELECT MAX(started_at_utc) FROM collection_runs ok WHERE ok.source=s.source_name AND ok.status='succeeded'),'0000')) AS consecutive_failures,
 (SELECT COUNT(*) FROM collection_runs r WHERE r.source=s.source_name AND status='quarantined') AS quarantine_count
FROM sources AS s;
