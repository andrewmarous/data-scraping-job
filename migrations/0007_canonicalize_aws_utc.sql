UPDATE aws_spot_prices
SET source_timestamp_utc=replace(source_timestamp_utc,'+00:00','Z')
WHERE source_timestamp_utc LIKE '%+00:00';

UPDATE gpu_instance_catalog
SET effective_from_utc=replace(effective_from_utc,'+00:00','Z'),
    effective_to_utc=replace(effective_to_utc,'+00:00','Z')
WHERE effective_from_utc LIKE '%+00:00' OR effective_to_utc LIKE '%+00:00';
