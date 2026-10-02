# Provider source setup

The service has one independent `launchd` job for each provider. A failed job does not stop another provider job.

Each job uses read-only HTTP requests. The jobs do not create, rent, stop, or remove compute resources.

## Data sources

| Source | Collected data | Authentication |
|---|---|---|
| Runpod | GPU types, Pod prices, and current Pod availability | Static API key in `RUNPOD_API_KEY` |
| Gcore | Regional GPU bare-metal flavors and available node counts | Static API token in `GCORE_API_KEY` |
| CLORE.AI | Marketplace servers, hardware, Spot prices, and rental state | Static API token in `CLORE_API_KEY` |
| SaladCloud | GPU classes, high-demand flags, and the published pricing document | Static API key in `SALAD_API_KEY` |
| Nebius | Published compute pricing document | None |
| Azure | Public Retail Prices API records for Spot GPU VMs | None |
| Google Cloud | Compute Engine Spot GPU SKUs and pricing expressions | Restricted API key in `GOOGLE_CLOUD_API_KEY` |
| CoreWeave | Published pricing document | None |

The source documents are [Runpod Catalog API](https://docs.runpod.io/api-reference-v2/catalog/list-gpu-types), [Gcore Spot GPU API](https://docs.gcore.com/edge-ai/ai-infrastructure/spot-bare-metal-gpu), and [CLORE.AI API](https://docs.clore.ai/gpu-marketplace/api).

The other source documents are [SaladCloud GPU classes](https://docs.salad.com/reference/saladcloud-api/organizations/list-gpu-classes), [Nebius pricing](https://docs.nebius.com/compute/resources/pricing), and [Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices).

The final source documents are [Google Cloud Pricing API](https://docs.cloud.google.com/billing/docs/how-to/get-pricing-information-api) and [CoreWeave pricing](https://www.coreweave.com/pricing).

## Credential to-do list

1. Create a Runpod API key that has read access to the catalog.
2. Add `RUNPOD_API_KEY` to `.env`.
3. Create a Gcore API token that has read access to Cloud GPU resources.
4. Add `GCORE_API_KEY` to `.env`.
5. Replace `REPLACE_WITH_GCORE_PROJECT_ID` in `config.toml`.
6. Replace the Gcore region list with all required region IDs.
7. Create a CLORE.AI API token.
8. Add `CLORE_API_KEY` to `.env`.
9. Create a SaladCloud API key.
10. Add `SALAD_API_KEY` to `.env`.
11. Replace `REPLACE_WITH_SALAD_ORGANIZATION` in `config.toml`.
12. Create a Google Cloud API key for the Cloud Billing Pricing API.
13. Restrict the Google key to the Cloud Billing Pricing API.
14. Add `GOOGLE_CLOUD_API_KEY` to `.env`.
15. Set the mode of `.env` to `0600`.

Do not use browser-login credentials. Do not use short-duration user tokens. These jobs cannot complete an interactive login flow.

## Activation procedure

Run these commands after you add the account data:

```bash
chmod 600 .env
uv run --env-file .env market-data verify-config
uv run --env-file .env market-data status
```

The launch agents are installed. You do not have to install them again after you edit `.env` or `config.toml`.

If one source reports `never`, run its command once. Read the error log if the command fails.

```bash
uv run --env-file .env market-data collect provider runpod
uv run --env-file .env market-data collect provider gcore
uv run --env-file .env market-data collect provider clore
uv run --env-file .env market-data collect provider saladcloud
uv run --env-file .env market-data collect provider nebius
uv run --env-file .env market-data collect provider azure
uv run --env-file .env market-data collect provider google_cloud
uv run --env-file .env market-data collect provider coreweave
```

The service archives each response before it inserts normalized rows. Schema errors copy the source response to the quarantine directory.
