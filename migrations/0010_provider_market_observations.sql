CREATE TABLE provider_market_observations(
 observation_id TEXT PRIMARY KEY,
 provider TEXT NOT NULL,
 collected_at_utc TEXT NOT NULL,
 source_key TEXT NOT NULL,
 evidence_class TEXT NOT NULL CHECK(evidence_class IN ('live_offer','live_capacity','catalog_price','product_catalog','source_document')),
 contract_type TEXT,
 product_name_source TEXT,
 accelerator_model_source TEXT,
 gpu_count INTEGER CHECK(gpu_count > 0 OR gpu_count IS NULL),
 gpu_memory_mb INTEGER,
 region_source TEXT,
 availability_text TEXT,
 available_count INTEGER CHECK(available_count >= 0 OR available_count IS NULL),
 price_text TEXT,
 currency TEXT,
 price_unit TEXT,
 package_json TEXT NOT NULL DEFAULT '{}',
 source_record_json TEXT NOT NULL,
 retrieval_id TEXT NOT NULL REFERENCES raw_retrievals(retrieval_id),
 UNIQUE(provider,retrieval_id,source_key)
);
CREATE INDEX provider_market_provider_time ON provider_market_observations(provider,collected_at_utc);
CREATE INDEX provider_market_gpu_time ON provider_market_observations(accelerator_model_source,collected_at_utc);

CREATE VIEW provider_market_latest AS
SELECT observation_id,provider,collected_at_utc,source_key,evidence_class,contract_type,
       product_name_source,accelerator_model_source,gpu_count,gpu_memory_mb,region_source,
       availability_text,available_count,price_text,currency,price_unit,package_json,source_record_json,retrieval_id
FROM (
 SELECT o.*,ROW_NUMBER() OVER(PARTITION BY provider,source_key ORDER BY collected_at_utc DESC,retrieval_id DESC) AS rank
 FROM provider_market_observations o
) WHERE rank=1;
