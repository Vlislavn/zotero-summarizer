# Optional provider search receipts

Transport/accounting contract used by the integrated Find new relevant flow.
Receipts do not assess scientific relevance, coverage or current availability.

## Call contract

`federate(plan, *, observe_outcomes=False, ...existing keyword arguments)` still
returns a list of `Candidate` objects. With the default false, existing leaf and
client calls receive **no additional keyword arguments**. Existing quotas,
OpenAlex's two-lexical-pass cap, provenance, deduplication, constraints, source
accounting and all other channels retain their behavior.

When explicitly enabled, each scheduled nonzero-allocation OpenAlex lexical or
semantic request owns a **distinct mutable receipt**. No receipt lives on a client, global variable or context variable.
The existing request dictionary retained at
`plan.retrieval_accounting[source]['variants']` gains:

```json
{
  "source": "openalex",
  "query": "existing public query",
  "allocation": 5,
  "channel": "openalex_semantic",
  "outcome": {"status": "success_empty", "count": 0, "error_code": null}
}
```

Receipt channels are exactly `openalex_lexical` and `openalex_semantic`.
Unscheduled variants and ordinary arXiv, Europe PMC, Crossref, Semantic Scholar,
OpenReview and library channels get no fabricated receipts. This Find version
schedules only OpenAlex; ordinary all-source Search remains unchanged. The source/request
legacy `status` and `observations` still describe candidate observations, **not
transport success**. Consumers must use the separate `outcome` for this purpose.
`_accounting.py` already retains complete request dictionaries; no second ledger
or serialization schema is required.

## Closed outcome values

| Status | Count | Error code |
|---|---|---|
| `success_empty` | `0` | null |
| `success_nonempty` | positive integer | null |
| `failed` | null | `rate_limited`, `http_error`, `transport_error`, `parse_error` |
| `not_attempted` | null | `network_disabled`, `client_unavailable` |
| `unknown` | null | null |

Counts are usable leaf hits before mutable family merging, cross-source dedup or
constraint rejection. They are never derived from another source or family counts.
A leaf that does not produce a receipt remains unknown even if it returns hits.
A direct blank query likewise produces no authoritative observation and stays
unknown without HTTP. Missing OpenAlex clients and cache-only clients are
not-attempted, not provider-empty.

`OpenAlexClient.search_works` and its transport `_get` accept optional
`outcome=None`. Other source leaves retain their existing signatures.
Receipts use only fixed fields/codes; no response body, raw exception, URL,
credential or private text is copied into them. The existing public `query` field
is intentionally retained unchanged; this seam does not authorize private queries.

## Valid payload versus parse failure

Receipt-mode success requires HTTP 200 and an OpenAlex `results` list.
An empty list is successful empty. Wrong containers, malformed rows, nonfinite
numbers and undecodable JSON fail with `parse_error`, not successful empty.
Ordinary/no-receipt decoding remains unchanged. A malformed row invalidates only
its request receipt, not independently usable neighboring variants.

These additional validation/error conversions apply only when a receipt is
requested. Ordinary leaf policy, including existing malformed-row exceptions,
remains intact. Non-search OpenAlex prestige/cache/author calls keep their prior
`_get` behavior and receive no receipt keyword. Unexpected federation channel
exceptions still propagate; this is not a retry engine or exception-policy cleanup.

## Verification boundaries

`tests/test_discovery_provider_leaves.py` uses HTTPX MockTransport with inert
XML/JSON/status/transport fixtures, including real leaf parsing and ordinary
non-search policy preservation. `tests/test_discovery_provider_federation.py`
passes through federation into retained plan accounting, holds three OpenAlex
requests alongside four unchanged ordinary-source requests, checks independent
receipt identity/channel/count retention after family merging and JSON round
trip, and protects narrow old mock signatures/all other channels. Marker assertions distinguish redacted receipts from the legitimate
existing public-query accounting field; they do not grade semantic meaning.

Integrated API and built-browser controls also exercise these actual adapters
through inert HTTP. See [the feature contract](discovery.md). These machine checks
do not qualify live providers, real models, private-user usefulness or scientific
recommendation quality. No standalone provider feature or automatic work is exposed.
