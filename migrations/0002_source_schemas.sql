CREATE TABLE vast_offer_snapshots(
 snapshot_id TEXT PRIMARY KEY, collected_at_utc TEXT NOT NULL, offer_id TEXT NOT NULL,
 machine_id TEXT, host_id TEXT, contract_type TEXT NOT NULL, rentable INTEGER CHECK(rentable IN (0,1) OR rentable IS NULL),
 gpu_name_source TEXT NOT NULL, gpu_count INTEGER NOT NULL CHECK(gpu_count > 0), gpu_ram_mb INTEGER,
 total_hourly_price_text TEXT, minimum_bid_text TEXT, currency TEXT, country_code TEXT, region TEXT,
 datacenter TEXT, reliability_text TEXT, duration_text TEXT, topology_json TEXT, network_json TEXT,
 storage_json TEXT, performance_json TEXT, source_offer_json TEXT NOT NULL,
 retrieval_id TEXT NOT NULL REFERENCES raw_retrievals(retrieval_id),
 UNIQUE(retrieval_id, offer_id, contract_type)
);
CREATE INDEX vast_snapshots_time ON vast_offer_snapshots(collected_at_utc, contract_type);
CREATE INDEX vast_snapshots_gpu ON vast_offer_snapshots(gpu_name_source, collected_at_utc);

CREATE TABLE aws_spot_prices(
 observation_id TEXT PRIMARY KEY, source_timestamp_utc TEXT NOT NULL, region TEXT NOT NULL,
 availability_zone TEXT NOT NULL, instance_type TEXT NOT NULL, product_description TEXT NOT NULL,
 currency TEXT NOT NULL, spot_price_text TEXT NOT NULL,
 retrieval_id TEXT NOT NULL REFERENCES raw_retrievals(retrieval_id),
 UNIQUE(source_timestamp_utc, availability_zone, instance_type, product_description, spot_price_text)
);
CREATE INDEX aws_spot_region_time ON aws_spot_prices(region, source_timestamp_utc);

CREATE TABLE gpu_instance_catalog(
 catalog_id TEXT PRIMARY KEY, provider TEXT NOT NULL, instance_type TEXT NOT NULL,
 effective_from_utc TEXT NOT NULL, effective_to_utc TEXT, accelerator_model TEXT NOT NULL,
 gpu_count INTEGER NOT NULL CHECK(gpu_count > 0), gpu_memory_mb INTEGER, topology_json TEXT,
 vcpu_count INTEGER, ram_mb INTEGER,
 source_retrieval_id TEXT REFERENCES raw_retrievals(retrieval_id),
 UNIQUE(provider, instance_type, effective_from_utc)
);
CREATE INDEX gpu_catalog_effective ON gpu_instance_catalog(provider, instance_type, effective_from_utc);
