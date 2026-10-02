CREATE VIEW source_run_summary AS
WITH sources(source) AS (VALUES('openrouter'),('vast'),('aws_spot'))
SELECT sources.source,
 (SELECT started_at_utc FROM collection_runs r WHERE r.source=sources.source ORDER BY started_at_utc DESC LIMIT 1) last_attempt,
 (SELECT finished_at_utc FROM collection_runs r WHERE r.source=sources.source AND status='succeeded' ORDER BY finished_at_utc DESC LIMIT 1) last_success,
 (SELECT status FROM collection_runs r WHERE r.source=sources.source ORDER BY started_at_utc DESC LIMIT 1) last_status,
 (SELECT COUNT(*) FROM collection_runs r WHERE r.source=sources.source AND status IN ('failed','quarantined')
   AND started_at_utc > COALESCE((SELECT MAX(started_at_utc) FROM collection_runs s WHERE s.source=sources.source AND s.status='succeeded'),'0000')) consecutive_failures,
 (SELECT COUNT(*) FROM collection_runs r WHERE r.source=sources.source AND status='quarantined') quarantine_count;

CREATE VIEW openrouter_model_share AS
SELECT observation_date, model_permaslug, CAST(total_tokens_text AS INTEGER) token_volume,
 CAST(total_tokens_text AS REAL) / NULLIF(SUM(CAST(total_tokens_text AS REAL)) OVER (PARTITION BY observation_date),0) model_share
FROM openrouter_daily_tokens WHERE is_current=1;

CREATE VIEW vast_offer_depth AS
SELECT collected_at_utc, gpu_name_source, contract_type, country_code, COUNT(*) offer_depth,
 SUM(gpu_count) rentable_gpu_count, MIN(CAST(total_hourly_price_text AS REAL)) minimum_node_price
FROM vast_offer_snapshots
GROUP BY collected_at_utc,gpu_name_source,contract_type,country_code;

CREATE VIEW vast_listing_history AS
SELECT offer_id,contract_type,gpu_name_source,MIN(collected_at_utc) first_seen_utc,
 MAX(collected_at_utc) last_seen_utc,COUNT(DISTINCT collected_at_utc) observed_polls
FROM vast_offer_snapshots GROUP BY offer_id,contract_type,gpu_name_source;

CREATE VIEW aws_spot_per_gpu AS
SELECT p.*, c.accelerator_model,c.gpu_count,c.gpu_memory_mb,c.topology_json,c.vcpu_count,c.ram_mb,
 CAST(p.spot_price_text AS REAL)/c.gpu_count spot_price_per_gpu
FROM aws_spot_prices p JOIN gpu_instance_catalog c
 ON c.provider='aws' AND c.instance_type=p.instance_type
 AND c.effective_from_utc<=p.source_timestamp_utc
 AND (c.effective_to_utc IS NULL OR c.effective_to_utc>p.source_timestamp_utc);

CREATE VIEW aws_region_coverage AS
SELECT region,availability_zone,substr(instance_type,1,instr(instance_type,'.')-1) instance_family,
 COUNT(*) observations,MAX(source_timestamp_utc) newest_observation
FROM aws_spot_prices GROUP BY region,availability_zone,instance_family;
