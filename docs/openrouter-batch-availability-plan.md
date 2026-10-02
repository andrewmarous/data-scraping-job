# OpenRouter batch-provider availability: implementation plan

## Goal and meaning

Collect a daily snapshot of providers listed for tracked OpenRouter `:batch` models. A listed provider means OpenRouter advertised that provider at collection time. It does **not** prove free capacity, request acceptance, or job completion. The collector must not submit batch jobs.

Use the existing `data-collection/market-data` SQLite database, raw-response store, run records, and daily scheduler. Keep this source separate from the existing OpenRouter daily-token collector so failures and cadence are independent.

## Confirmed sources

- Rankings: authenticated `GET https://openrouter.ai/api/v1/datasets/rankings-daily?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`. The seven completed UTC days return daily `model_permaslug` and `total_tokens`, plus `meta.as_of` and date bounds. The sample in `market-rankings.log` has no `:batch` suffix in its permaslugs.
- Catalog: `GET https://openrouter.ai/api/v1/models`. Join `canonical_slug` to the ranking permaslug and select catalog IDs ending in `:batch`. A canonical slug can map to both a standard and a batch ID. Use the seven-day sum as the agreed discovery proxy, not proof that all counted tokens came from batch requests. Do not split or multiply tokens across variants. Deduplicate batch IDs before ranking.
- Provider list: `GET https://openrouter.ai/api/v1/models/{encoded_model_id}/endpoints`. The endpoint array matched the provider rows on three sampled batch-model pages. One sampled model returned a successful empty array on both sources. Encode the colon in the model ID and retain the slash.

API responses can change. Store raw responses, source URLs, retrieval times, and parser versions. Do not parse page HTML in the normal collection path.

## Daily flow

1. Run once per UTC day after the previous UTC day is complete. Request the preceding seven completed UTC dates, inclusive.
2. Fetch the catalog and the ranking dataset. Validate response types and required fields before changing tracked models. Use exact `canonical_slug` matches. Sum integer token strings across the window. Reject negative or invalid values. Sort by total descending, then model ID for stable ties.
3. Select the top **10 batch model IDs before publisher filtering**. Save their ranks, token totals, date range, `as_of`, and publisher decisions. Do not refill excluded positions with models below rank 10.
4. Add new model IDs to the tracking set unless the ID publisher prefix (before `/`, case-insensitive) is `openai`, `anthropic`, or `google`. Treat every other prefix as eligible for this pipeline. This rule is a tracking convention, not verification of an open-weight license. An initial seed list is optional. The first run can start with an empty tracking set.
5. Poll the endpoints API for every active tracked model, even after it leaves the top 10. Store one observation per returned endpoint, with provider name, endpoint tag, status, and relevant pricing fields as reported. Preserve all endpoint variants for a provider.
6. Record a successful empty response as `listed_count=0`. Do not infer an empty list from a timeout, authentication error, non-200 response, or invalid JSON. Do not remove tracked models automatically when they disappear from rankings or the catalog.

The ranking feed can omit a model that appears in the catalog. Record unmatched ranking slugs for review. If either discovery response fails, skip discovery changes, but still poll previously tracked models. If a model poll fails, retain its last successful observation without labeling it currently available.

## SQLite changes

Add a migration with these logical tables:

- `batch_tracked_models`: stable model ID, canonical slug at discovery, publisher prefix, first seen, tracking state, optional manual reason.
- `batch_ranking_snapshots` and `batch_ranking_entries`: UTC collection date, window, source `as_of`, rank, model ID, summed tokens, publisher-filter result. Keep past snapshots rather than replacing them.
- `batch_endpoint_polls`: model ID, retrieval ID, attempt time, outcome (`success`, `empty`, `http_error`, `parse_error`, `network_error`), and listed count.
- `batch_endpoint_observations`: poll ID, provider name, endpoint tag, status, selected structured fields, and raw endpoint JSON. Uniqueness applies within one poll, not across days.
- `batch_discovery_reviews`: unmatched ranking slugs and malformed catalog identifiers, with reason and first/last seen times. Missing slugs do not prevent collection for known models.

Link every HTTP attempt to `raw_retrievals` and its `collection_runs` record where a response body exists. Preserve HTTP status and errors for attempts without a usable body. Keep the existing token tables unchanged. Index observations by model ID, poll time, and provider/tag. Use a dedicated `openrouter_batch` source label and parser version.

A historical query must use successful polls only. Compare consecutive successful polls to identify listed, removed, and newly listed endpoints. Report stale data if the latest poll failed. A status value alone is not a substitute for presence in the endpoint array. Record endpoint status as reported without guessing what its numeric values mean.

## Integration and safety

Add a separate `collect openrouter-batch` command, configuration flag, `collect due` entry, and daily launchd job. Reuse the existing `OPENROUTER_API_KEY`, request timeouts, size limits, retries with backoff, raw storage, locks, and structured logging. Never log the API key. Bound the number of retries and requests. Use UTC collection date as the daily partition key. Skip an already successful partition on routine reruns. On explicit retry, retain the previous run and its polls, and record new retrieval IDs and timestamps. Mark a run successful only after all tracked models have a successful poll. A partial failure remains visible and the next retry polls failed models again.

Alert when ranking or catalog parsing fails, unmatched slugs appear, endpoint polling fails, or many previously listed endpoints disappear in one run. A successful zero-provider poll is data, not a collection error. Require review before treating a sudden broad disappearance as a market event.

## Tests and acceptance

- Fixtures cover the authenticated ranking response, the catalog join, shared standard/batch canonical slugs, duplicate daily rows, tied totals, an unmatched slug, and a malformed token count. Reject duplicate model/date rows instead of silently doubling tokens.
- Fixtures cover a one-provider model, multiple tags from one provider, a successful empty endpoint list, an HTTP error, and malformed JSON.
- Integration tests cover reruns, missing discovery data with continued polling, retention after a ranking exit, raw-response links, and independent scheduling.
- Compare a sample of API endpoint arrays with batch model pages. The prior three-model comparison supplies the initial acceptance evidence.
- In a dry run, inspect top-10 ranking entries and confirm that known batch leaders map to catalog IDs. Inspect stored endpoint observations against their raw API responses. Confirm that no job-submission API call occurs.

No job completion or actual capacity metric is part of this pipeline.
