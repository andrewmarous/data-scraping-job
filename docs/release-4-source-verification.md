# Release 4 source verification

Reviewed: 2026-08-31

## Outcome

No candidate is enabled. Release 4 stops at its required entry gate. The application does not create `gpu_index_prices`, call an undocumented endpoint, or scrape a dashboard.

Run the local decision report:

```bash
uv run market-data indexes status
uv run market-data indexes status --json
```

## ComputePrices

Status: `blocked_rights`.

The official API documentation verifies a versioned JSON API, bearer authentication, a public OpenAPI schema, cursor pagination, rate limits, price and contract fields, and tier-dependent history. The documented endpoints include `GET /api/v1/gpu-prices` and `GET /api/v1/gpu-prices/history`.

The service cannot be enabled yet because this project requires indefinite immutable raw-response storage and may produce exports. ComputePrices prohibits redistribution of its raw feed and prohibits systematic archival that reconstructs history outside a tier's entitlement. Public display also requires attribution. Obtain written terms that cover this service's retention, backup, derived exports, and intended publication before implementation.

Official source: <https://computeprices.com/docs/api>

To reconsider this decision, record:

1. the licensed account tier;
2. written archival and backup permission;
3. permitted derived exports and public display;
4. required attribution;
5. retention and deletion requirements; and
6. captured OpenAPI and sanitized response fixtures.

## GPU.ai

Status: `unverified_interface`.

The public site presents GPU.ai as a GPU cloud marketplace and links product documentation, pricing, and a supplier API. It does not establish an authorized, stable, read-only pricing-index endpoint with history, pagination, rate limits, availability semantics, or republication rights. A web pricing page is not sufficient for this release.

Official sources: <https://gpu.ai/> and <https://gpu.ai/docs>

To reconsider this decision, obtain official consumer API documentation and terms that verify every Release 4 gate. Do not inspect private console traffic or scrape the dashboard.

## Data interpretation

If a source passes later, its rows will describe advertised catalog prices or source-reported availability. They will not represent realized transaction prices, completed rentals, or direct demand.
