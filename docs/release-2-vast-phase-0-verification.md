# Release 2 Phase 0: Vast.ai offer search verification

Verified: 2026-08-31

## Operational addendum

On 2026-08-31, the operator determined that the planned personal-use collection is permitted.
The service now enables the read-only collector on that basis.

The original rights and credential-scope findings remain below as the verification record.

## Decision

**Conditionally verified; do not enable collection yet.** The supported offer-search API, three contract values, response shape, practical response size, endpoint rate-limit behavior, short-term identifier behavior, and bundling behavior were verified with non-mutating requests. The replacement credential can search offers, but it can still read the account's API-key list despite being configured as `misc`-only. In addition, Vast's Terms of Use prohibit systematic retrieval that creates a collection or database without written permission. Scheduled archival therefore remains blocked.

No rental, bid, instance, billing, key-creation, key-deletion, or other mutation endpoint was called.

## Supported interface

Use the official REST operation:

- `POST https://console.vast.ai/api/v0/bundles/`
- `Authorization: Bearer $VAST_API_KEY`
- `Content-Type: application/json`

The official CLI and SDK call the same `/bundles/` operation. Direct REST is preferred for this service because it lets the existing raw archive retain the exact response bytes and avoids adding the broad Vast SDK/CLI dependency.

The newer `PUT /api/v0/search/asks/` interface also exists in the official CLI source, but it is not the documented stable API operation. A live probe confirmed that it requires the wrapper shape `{"select_cols":["*"],"q":...}`; the old body shape is rejected. Do not use it for the first collector.

Recommended request body:

```json
{
  "verified": {"eq": true},
  "external": {"eq": false},
  "rentable": {"eq": true},
  "rented": {"eq": false},
  "order": [["id", "asc"]],
  "type": "on-demand",
  "limit": 10000,
  "allocated_storage": 5.0
}
```

`allocated_storage` affects displayed total price and must be fixed and recorded with every retrieval. The CLI defaults to 5 GiB. `disable_bundling` is described as deprecated in the current official CLI source and is more heavily rate-limited in older CLI documentation. A live request with `disable_bundling=true` returned HTTP 400, `invalid_args`, with `Unrecognized argument: disable_bundling`. The collector cannot request unbundled results through this operation.

## Contract mapping

| Source request value | Local value | Meaning |
|---|---|---|
| `on-demand` | `on-demand` | Fixed listed pricing |
| `bid` | `interruptible` | Minimum-bid pricing; can be interrupted if outbid |
| `reserved` | `reserved` | Reserved pricing |

The official CLI aliases `interruptible` to source value `bid`. The API documentation also shows `ondemand` in one enum while official CLI source sends `on-demand`; use the CLI's canonical `on-demand` spelling.

Live authenticated requests succeeded for `ondemand`, `bid`, and `reserved`. The implementation must use the canonical values above and test them in an opt-in integration test.

## Authentication and permissions

All offer searches require a bearer API key. Vast permissions place **Search Offers** in the `misc` category. The target collector key therefore needs only:

```json
{"api":{"misc":{}}}
```

Do not grant `instance_write`, `billing_write`, `machine_write`, `user_write`, or team-write categories. In particular, absence of `instance_write` is the control that prevents creating/renting, stopping, or destroying instances.

After the credential was replaced with a console-configured `misc`-only key, verification was repeated:

1. `POST /api/v0/bundles/` returned 200 with 512 offers and `truncated=false`.
2. `GET /api/v0/auth/apikeys/` also returned 200, although the permissions reference classifies **Show API Keys** under `user_read`.

This means the server does not provide the expected denial proof for this key. The console configuration is evidence of intended scope, but the key cannot be certified as technically restricted to offer search. This discrepancy should be reported to Vast support. Do **not** test denial by calling a rental, bid, key-management, or other mutation endpoint.

## Limits, pagination, and completeness

The API accepts `limit` and returns top-level `offers` and `truncated`. No cursor, offset, page token, or documented maximum was found in the current OpenAPI operation.

A live on-demand request with `limit=10000` returned:

- HTTP 200;
- 512 offers;
- `truncated=false`; and
- approximately 1.51 MB of JSON.

This indicates that a single high-limit request can currently return the complete bundled result set. It is not a permanent maximum guarantee. The collector must reject or quarantine `truncated=true`; it must not silently treat that response as a complete market snapshot. It must also cap response bytes.

Live response headers exposed `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset`. Offer probes effectively exhausted a short-burst bucket after each call. Vast documents endpoint- and identity-specific limits, HTTP 429 on excess, and no `Retry-After` header. Keep contract requests sequential, spread them over time, and apply bounded jittered backoff to 429 responses. The planned 5- and 10-minute polling intervals are comfortably below the observed limit when jobs are not launched simultaneously.

## Response schema and identifiers

Each sampled contract returned an object with `offers` and `truncated`; each sampled offer had 100 fields. Sanitized one-offer fixtures are committed at:

- `tests/fixtures/vast/ondemand.json`
- `tests/fixtures/vast/bid.json`
- `tests/fixtures/vast/reserved.json`

Important fields observed include:

- identity: `id`, `ask_contract_id`, `bundle_id`, `machine_id`, `host_id`;
- contract and state: `is_bid`, `rentable`, `rented`, `duration`, `time_remaining`;
- GPU: `gpu_name`, `num_gpus`, `gpu_ram`, `gpu_total_ram`, `gpu_frac`, `gpu_arch`;
- pricing: `dph_total`, `dph_base`, `min_bid`, `storage_cost`, ingress/egress costs, discounts;
- geography: `geolocation`, `geolocode`, `datacenter`, `hosting_type` where present;
- reliability and performance: `reliability`, `reliability2`, `expected_reliability`, `dlperf`, network and disk bandwidth;
- topology: `bw_nvlink`, `pcie_bw`, `pci_gen`, GPU lanes;
- host-sensitive fields: `public_ipaddr`, `hostname`, `host_id`, and GPU IDs.

The API documentation describes `id` as offer ID and `machine_id` as physical machine ID. In the samples, `id == ask_contract_id`; `bundle_id`, `machine_id`, and `host_id` were distinct. A three-snapshot deterministic-order test then observed 512 offers in each snapshot. Across the three snapshots, 321 offer IDs were common and 703 were present in at least one snapshot; 382 IDs appeared or disappeared. For all 321 common IDs, `ask_contract_id`, `bundle_id`, `machine_id`, and `host_id` remained unchanged. All compared fields also remained unchanged. This supports using `id` as the snapshot-level offer key and retaining the other IDs as stable linkage fields, while treating disappearance only as offer churn—not as a rental. It does not prove long-term identifier permanence. Never expose host-sensitive fields in exported fixtures or routine reports.

The response allows additional properties. Parsing must require the normalization-critical fields and preserve the full source offer object, but it must not fail only because a new optional field appears. Missing, null, or type-changed critical fields must quarantine the whole response.

## Implementation requirements discovered

1. Add the endpoint and fixed allocated-storage value to configuration or collector constants.
2. Issue one independent request per canonical contract type.
3. Use deterministic ordering and a high explicit limit.
4. Require `truncated=false`; quarantine truncated or unexpectedly empty snapshots.
5. Retain complete raw bytes before parsing.
6. Preserve source-native floating-point spellings from raw JSON; convert normalized prices through decimal text, not binary float.
7. Store snapshots even when unchanged.
8. Treat bundled search results as advertised offers, not physical GPU quantity or completed rentals.
9. Redact IP addresses, hostnames, API keys, and authorization data from fixtures and logs.
10. Run contract requests sequentially and honor bounded retry budgets.

## Data rights

The [Vast.ai Terms of Use](https://vast.ai/terms) (version dated 2025-11-10) prohibit systematic retrieval of data or content to create or compile a collection, compilation, database, or directory without written permission from Vast. The planned five- and ten-minute snapshot archive is systematic retrieval designed to create a historical database. An official API and valid key do not override this express restriction.

Obtain written permission from Vast for the proposed cadence, indefinite local raw retention, normalized historical storage, internal analysis, and any intended publication or redistribution. Preserve that permission with this verification record. Until then, do not run scheduled collection or build a historical archive.

## Remaining gate before implementation can be enabled

- Obtain written permission from Vast for systematic historical collection, retention, analysis, and the intended output-sharing model.
- Ask Vast support why a `misc`-only key can access `GET /api/v0/auth/apikeys/`; obtain confirmation that it cannot invoke instance, billing, machine, user, or team mutations.
- Accept and document that `/bundles/` returns a bundled offer view. The API rejects `disable_bundling`, so the resulting metric must be named bundled offer depth and must not be represented as complete physical-machine inventory.

The short-term identifier test is complete. Fixtures and collector development may proceed offline, but scheduled collection must remain disabled until the rights and credential-enforcement issues are resolved.

## Sources

- [Vast.ai search offers API](https://docs.vast.ai/api-reference/search/search-offers)
- [Vast.ai CLI search offers](https://docs.vast.ai/cli/reference/search-offers)
- [Vast.ai permissions](https://docs.vast.ai/api-reference/permissions-and-authorization)
- [Vast.ai API key management](https://docs.vast.ai/guides/reference/api-keys)
- [Vast.ai rate limits and errors](https://docs.vast.ai/sdk/python/rate-limits)
- [Official Vast.ai CLI/SDK repository](https://github.com/vast-ai/vast-cli)
