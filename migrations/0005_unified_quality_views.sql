CREATE TABLE mapping_versions(
 version_id TEXT PRIMARY KEY, created_at_utc TEXT NOT NULL, description TEXT NOT NULL
);
INSERT INTO mapping_versions VALUES('v1','2026-09-01T00:00:00Z','Initial explicit canonical mappings');

CREATE TABLE canonical_mappings(
 mapping_id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES mapping_versions(version_id),
 dimension TEXT NOT NULL, source TEXT NOT NULL, source_value TEXT NOT NULL,
 canonical_value TEXT NOT NULL, effective_from_utc TEXT NOT NULL, effective_to_utc TEXT,
 UNIQUE(version_id,dimension,source,source_value)
);
INSERT INTO canonical_mappings VALUES
 ('provider-openrouter','v1','provider','openrouter','OpenRouter','openrouter','2026-09-01T00:00:00Z',NULL),
 ('provider-vast','v1','provider','vast','Vast.ai','vast','2026-09-01T00:00:00Z',NULL),
 ('provider-aws','v1','provider','aws_spot','AWS','aws','2026-09-01T00:00:00Z',NULL),
 ('contract-ondemand','v1','contract','vast','on-demand','on-demand','2026-09-01T00:00:00Z',NULL),
 ('contract-bid','v1','contract','vast','interruptible','interruptible','2026-09-01T00:00:00Z',NULL),
 ('contract-reserved','v1','contract','vast','reserved','reserved','2026-09-01T00:00:00Z',NULL),
 ('contract-spot','v1','contract','aws_spot','spot','spot','2026-09-01T00:00:00Z',NULL),
 ('currency-vast-usd','v1','currency','vast','USD','USD','2026-09-01T00:00:00Z',NULL),
 ('currency-aws-usd','v1','currency','aws_spot','USD','USD','2026-09-01T00:00:00Z',NULL);

CREATE TABLE source_schedule_expectations(
 source TEXT NOT NULL, partition_key TEXT NOT NULL, interval_minutes INTEGER NOT NULL CHECK(interval_minutes > 0),
 updated_at_utc TEXT NOT NULL, PRIMARY KEY(source,partition_key)
);

CREATE VIEW openrouter_token_volume_quality AS
WITH ranked AS (
 SELECT observation_date,model_permaslug,CAST(total_tokens_text AS INTEGER) token_volume,
 ROW_NUMBER() OVER(PARTITION BY observation_date ORDER BY CAST(total_tokens_text AS INTEGER) DESC) rank_number
 FROM openrouter_daily_tokens WHERE is_current=1
), totals AS (
 SELECT observation_date,SUM(token_volume) total_token_volume,
 SUM(CASE WHEN rank_number<=50 THEN token_volume ELSE 0 END) top_50_token_volume
 FROM ranked GROUP BY observation_date
)
SELECT *,CAST(top_50_token_volume AS REAL)/NULLIF(total_token_volume,0) top_50_share FROM totals;

CREATE VIEW openrouter_model_day_change AS
SELECT observation_date,model_permaslug,CAST(total_tokens_text AS INTEGER) token_volume,
 CAST(total_tokens_text AS INTEGER)-LAG(CAST(total_tokens_text AS INTEGER)) OVER(PARTITION BY model_permaslug ORDER BY observation_date) day_over_day_change
FROM openrouter_daily_tokens WHERE is_current=1;

CREATE VIEW vast_price_observations AS
SELECT collected_at_utc,gpu_name_source,country_code,contract_type,gpu_count,
 CAST(total_hourly_price_text AS REAL) complete_node_price,
 CAST(total_hourly_price_text AS REAL)/gpu_count price_per_gpu,
 PERCENT_RANK() OVER(PARTITION BY collected_at_utc,gpu_name_source,country_code,contract_type ORDER BY CAST(total_hourly_price_text AS REAL)) price_percent_rank
FROM vast_offer_snapshots WHERE total_hourly_price_text IS NOT NULL;

CREATE VIEW vast_minimum_bid_movement AS
SELECT collected_at_utc,offer_id,gpu_name_source,CAST(minimum_bid_text AS REAL) minimum_bid,
 CAST(minimum_bid_text AS REAL)-LAG(CAST(minimum_bid_text AS REAL)) OVER(PARTITION BY offer_id ORDER BY collected_at_utc) minimum_bid_change
FROM vast_offer_snapshots WHERE contract_type='interruptible' AND minimum_bid_text IS NOT NULL;

CREATE VIEW source_poll_completeness AS
SELECT e.source,e.partition_key,e.interval_minutes,
 MIN(r.started_at_utc) first_attempt,MAX(r.started_at_utc) last_attempt,
 CAST((julianday('now')-julianday(MIN(r.started_at_utc)))*1440/e.interval_minutes AS INTEGER)+1 expected_polls,
 SUM(CASE WHEN r.status='succeeded' THEN 1 ELSE 0 END) completed_polls,
 SUM(CASE WHEN r.status IN ('failed','quarantined') THEN 1 ELSE 0 END) failed_polls
FROM source_schedule_expectations e LEFT JOIN collection_runs r
 ON r.source=e.source AND r.partition_key=e.partition_key
GROUP BY e.source,e.partition_key,e.interval_minutes;

CREATE VIEW source_data_quality AS
SELECT s.*,
 CASE s.source WHEN 'openrouter' THEN (SELECT COUNT(*) FROM openrouter_daily_tokens WHERE supersedes_observation_id IS NOT NULL) ELSE 0 END revision_count,
 (SELECT COUNT(*) FROM raw_retrievals rr WHERE rr.source=s.source AND rr.error_class IS NOT NULL) retrieval_error_count
FROM source_run_summary s;

CREATE VIEW unmapped_source_values AS
SELECT 'gpu' dimension,'vast' source,gpu_name_source source_value,COUNT(*) observations
FROM vast_offer_snapshots v WHERE NOT EXISTS(
 SELECT 1 FROM canonical_mappings m WHERE m.dimension='gpu' AND m.source='vast' AND m.source_value=v.gpu_name_source AND m.effective_to_utc IS NULL)
GROUP BY gpu_name_source
UNION ALL
SELECT 'gpu','aws_spot',accelerator_model,COUNT(*) FROM gpu_instance_catalog c WHERE NOT EXISTS(
 SELECT 1 FROM canonical_mappings m WHERE m.dimension='gpu' AND m.source='aws_spot' AND m.source_value=c.accelerator_model AND m.effective_to_utc IS NULL)
GROUP BY accelerator_model;

CREATE VIEW cross_cloud_price_comparison AS
SELECT collected_at_utc observed_at_utc,'vast' source,gpu_name_source gpu_source,
 contract_type,currency,total_hourly_price_text complete_node_price,gpu_count,
 CAST(total_hourly_price_text AS REAL)/gpu_count price_per_gpu,topology_json,
 CASE WHEN contract_type='interruptible' THEN 1 ELSE 0 END revocable,NULL fees_text,duration_text term_text
FROM vast_offer_snapshots WHERE total_hourly_price_text IS NOT NULL
UNION ALL
SELECT source_timestamp_utc,'aws_spot',accelerator_model,'spot',currency,spot_price_text,gpu_count,
 spot_price_per_gpu,topology_json,1,NULL,NULL FROM aws_spot_per_gpu;
