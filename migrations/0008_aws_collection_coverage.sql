CREATE TABLE aws_collection_coverage(
 coverage_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
 collected_at_utc TEXT NOT NULL, region TEXT NOT NULL, instance_family TEXT NOT NULL,
 offered_instance_types_json TEXT NOT NULL, offered_instance_type_count INTEGER NOT NULL,
 source_observation_count INTEGER NOT NULL, newest_source_observation_utc TEXT,
 offering_retrieval_id TEXT NOT NULL REFERENCES raw_retrievals(retrieval_id),
 UNIQUE(run_id,region,instance_family)
);

DROP VIEW aws_region_coverage;
CREATE VIEW aws_region_coverage AS
SELECT region,instance_family,collected_at_utc,offered_instance_types_json,
 offered_instance_type_count,source_observation_count,newest_source_observation_utc,
 CASE WHEN offered_instance_type_count=0 THEN 1 ELSE 0 END empty_offering_family,
 CASE WHEN source_observation_count=0 THEN 1 ELSE 0 END empty_price_history
FROM aws_collection_coverage;
