DROP VIEW source_poll_completeness;

CREATE VIEW source_poll_completeness AS
SELECT e.source,e.partition_key,e.interval_minutes,
 MIN(r.started_at_utc) first_attempt,MAX(r.started_at_utc) last_attempt,
 CAST((julianday('now')-julianday(MIN(r.started_at_utc)))*1440/e.interval_minutes AS INTEGER)+1 expected_polls,
 SUM(CASE WHEN r.status='succeeded' THEN 1 ELSE 0 END) completed_polls,
 SUM(CASE WHEN r.status IN ('failed','quarantined') THEN 1 ELSE 0 END) failed_polls
FROM source_schedule_expectations e LEFT JOIN collection_runs r
 ON r.source=e.source AND (r.partition_key=e.partition_key OR e.partition_key='daily')
GROUP BY e.source,e.partition_key,e.interval_minutes;
