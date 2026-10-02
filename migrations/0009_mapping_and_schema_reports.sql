CREATE VIEW source_version_report AS
SELECT source,collector_version,parser_version,COUNT(*) retrievals,
 MIN(retrieved_at_utc) first_retrieval,MAX(retrieved_at_utc) last_retrieval
FROM raw_retrievals GROUP BY source,collector_version,parser_version;

DROP VIEW unmapped_source_values;
CREATE VIEW unmapped_source_values AS
SELECT 'gpu' dimension,'vast' source,gpu_name_source source_value,COUNT(*) observations
FROM vast_offer_snapshots v WHERE NOT EXISTS(
 SELECT 1 FROM canonical_mappings m WHERE m.dimension='gpu' AND m.source='vast' AND m.source_value=v.gpu_name_source AND m.effective_to_utc IS NULL)
GROUP BY gpu_name_source
UNION ALL
SELECT 'gpu','aws_spot',accelerator_model,COUNT(*) FROM gpu_instance_catalog c WHERE NOT EXISTS(
 SELECT 1 FROM canonical_mappings m WHERE m.dimension='gpu' AND m.source='aws_spot' AND m.source_value=c.accelerator_model AND m.effective_to_utc IS NULL)
GROUP BY accelerator_model
UNION ALL
SELECT 'region','vast',region,COUNT(*) FROM vast_offer_snapshots v WHERE region IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM canonical_mappings m WHERE m.dimension='region' AND m.source='vast' AND m.source_value=v.region AND m.effective_to_utc IS NULL)
GROUP BY region
UNION ALL
SELECT 'region','aws_spot',region,COUNT(*) FROM aws_spot_prices p WHERE NOT EXISTS(
 SELECT 1 FROM canonical_mappings m WHERE m.dimension='region' AND m.source='aws_spot' AND m.source_value=p.region AND m.effective_to_utc IS NULL)
GROUP BY region
UNION ALL
SELECT 'package','aws_spot',instance_type,COUNT(*) FROM aws_spot_prices p WHERE NOT EXISTS(
 SELECT 1 FROM canonical_mappings m WHERE m.dimension='package' AND m.source='aws_spot' AND m.source_value=p.instance_type AND m.effective_to_utc IS NULL)
GROUP BY instance_type;

DROP VIEW cross_cloud_price_comparison;
CREATE VIEW cross_cloud_price_comparison AS
SELECT collected_at_utc observed_at_utc,'vast' source,gpu_name_source gpu_source,
 contract_type,currency,total_hourly_price_text complete_node_price,gpu_count,
 CAST(total_hourly_price_text AS REAL)/gpu_count price_per_gpu,topology_json,
 CASE WHEN contract_type='interruptible' THEN 1 ELSE 0 END revocable,NULL fees_text,duration_text term_text,
 (SELECT group_concat(version_id,',') FROM mapping_versions) mapping_versions
FROM vast_offer_snapshots WHERE total_hourly_price_text IS NOT NULL
UNION ALL
SELECT source_timestamp_utc,'aws_spot',accelerator_model,'spot',currency,spot_price_text,gpu_count,
 spot_price_per_gpu,topology_json,1,NULL,NULL,(SELECT group_concat(version_id,',') FROM mapping_versions)
FROM aws_spot_per_gpu;
