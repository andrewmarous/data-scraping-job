-- Live offer snapshot, not a guarantee that a launch will succeed.
-- All rows retain source price units and the exact offered GPU count.
CREATE VIEW vast_hopper_blackwell_offers AS
SELECT collected_at_utc, offer_id, machine_id, host_id, gpu_name_source,
       gpu_count, gpu_ram_mb, total_hourly_price_text, currency,
       country_code, region, datacenter, reliability_text, topology_json,
       source_offer_json, retrieval_id
FROM vast_offer_snapshots
WHERE contract_type = 'on-demand' AND rentable = 1
  AND (upper(gpu_name_source) LIKE '%H100%' OR upper(gpu_name_source) LIKE '%H200%'
       OR upper(gpu_name_source) LIKE '%B200%' OR upper(gpu_name_source) LIKE '%B300%'
       OR upper(gpu_name_source) LIKE '%GB200%' OR upper(gpu_name_source) LIKE '%GB300%');

-- Catalog rows have different evidence classes: do not treat a published SKU
-- or price as a measured available instance.
CREATE VIEW provider_hopper_blackwell_observations AS
SELECT provider, collected_at_utc, source_key, evidence_class, contract_type,
       product_name_source, accelerator_model_source, gpu_count, gpu_memory_mb,
       region_source, availability_text, available_count, price_text,
       currency, price_unit, source_record_json, retrieval_id
FROM provider_market_observations
WHERE lower(coalesce(contract_type,'')) = 'on-demand'
  AND (upper(coalesce(accelerator_model_source,'')) LIKE '%H100%'
       OR upper(coalesce(accelerator_model_source,'')) LIKE '%H200%'
       OR upper(coalesce(accelerator_model_source,'')) LIKE '%B200%'
       OR upper(coalesce(accelerator_model_source,'')) LIKE '%B300%'
       OR upper(coalesce(accelerator_model_source,'')) LIKE '%GB200%'
       OR upper(coalesce(accelerator_model_source,'')) LIKE '%GB300%');
