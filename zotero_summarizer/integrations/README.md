# integrations — external-system adapters

Thin, low-level clients for everything outside the app: the local Zotero
SQLite DB, PDFs, the LLM, OpenAlex (prestige), Unpaywall (OA PDFs), and the
Targeted Search sources (arXiv/EuropePMC/Crossref/Semantic Scholar + the
authenticated OpenReview peer-review channel). No business logic — just I/O with
clear types.

```
services/ ─calls→ integrations/ ─talks to→  Zotero DB | PDFs | LLM API | OpenAlex | Unpaywall
```

| file | responsibility |
|---|---|
| `zotero_read.py` | `ZoteroReader`: read-only WAL-aware connections, bounded lock retries and user-library collection helpers. Persistent locks raise; no raw snapshot copy or immutable WAL-skipping mode. Statistics scope collections/tags to the user library and exclude trashed items/attachments. `get_collections` scopes its collection and item counts; `collection_name_for_key` validates picker keys. Collection sorting pins Inbox and read-next, then alphabetical, recursively. |
| `_zotero_read_items.py` · `_zotero_read_lookup.py` · `_zotero_read_feeds.py` | reader query mixins (items/detail — now also selects Zotero `typeName` as `item_type`, a weak paper-type prior for deep-review type detection, and `publicationTitle` as `venue` so the Library can filter by journal · `get_items` (paged, 500/call) + `get_all_items` (whole-library passes) share one SQL builder (`_build_list_query`) + row mapper (`_row_to_item`); `get_all_items` runs that query UN-paged in a SINGLE `_execute_read`, so a whole-library read uses one connection/query rather than repeated 500-item pages · find/membership/tags — DOI dedup matches all `domain.normalize_doi` variants · feeds — incl. `get_unread_feed_guid_map` (guid → unread `feedItems.itemID`; the read-sync reconciler's join key, see `services/triage/feeds/_zotero_readsync.py`)) |
| `app_rss.py` | `AppRssReader`: app-owned RSS items and rotating bounded refresh; offline returns before network/DB writes. Refresh requires a valid RSS/Atom document, rolls back the failed feed, records its error and raises. Text is sanitized before DOI/key derivation and storage. `stream_public_url` is shared with PDF fetching: validated numeric destination + original Host/TLS identity on every hop. |
| `_zotero_read_common.py` | `ZoteroReadError` + arXiv/sanitize helpers + `_NON_BIBLIOGRAPHIC_TYPES_SQL` (the single `('attachment','note','annotation')` exclusion every "library items" query shares so PDF annotations never appear as papers) + `_USER_LIBRARY_ID_SELECT` (the single `type='user'` library scope every whole-library read injects — Zotero keeps ~dozens of `type='feed'` RSS libraries in the same `items` table, so an unscoped read leaks feed items into the corpus/ranker/tag writes/full-text; this caused cross-library 403 attachments) (leaf) |
| `zotero_write.py` | `ZoteroWriter`: WAL-consistent backup and apply dispatcher. An explicit transaction encloses per-row savepoints; lock errors roll back the entire batch before retry, invalid rows return individual failures, and unexpected errors propagate. WAL setup failures are not swallowed. Backup pruning follows a successful write. |
| `_zotero_write_items.py` · `_zotero_write_feed.py` · `_zotero_write_fields.py` · `_zotero_write_attachments.py` · `_zotero_write_tags.py` · `_zotero_write_collections.py` | writer mixins (item creation/materialization · idempotent feed-read bookkeeping · `set_field` single-field upsert e.g. Call Number · native imported_url attachment · tag/note helpers · collections) |
| `_zotero_write_common.py` | `ZoteroWriteError` + LOGGER + `resolve_user_library_item_id` — the single guard every write that targets an item by key routes through, scoping resolution to `type='user'` so a feed item's key can never be mutated/parented into the user library (`required=False` for the best-effort batch remove). Mirrors `_USER_LIBRARY_ID_SELECT` on the read side (leaf) |
| `pdf.py` · `pdf_fetch.py` | extract local PDFs; fetch OA PDFs with size/timeout/magic caps. Automatic destinations/redirects use the shared public-IP pinning boundary; proxy env is ignored. Cached paths remain usable offline. |
| `browser_fetch.py` | Institutional-access PDF acquisition with an optional Patchright/Playwright Chromium browser and persistent login profile. Native response interception reads bounded CDP streams, preserves HTML/JS/cookies, and follows citation metadata plus Download PDF links; no unbounded context-request or response-body API. PDF bytes are identified by magic even when the main document has a wrong MIME type. Declared-but-unavailable PDFs do not become rendered paywall stubs. Web articles without declared PDFs can use bounded streamed print output; `render_article_pdf` uses an ephemeral context and a distinct `render:` cache key. Browser sessions share the public-only proxy and one lock; `channel` selects Chrome/bundled Chromium. Optional cookie-store import and headed login readiness remain here. Missing dependency/non-PDF returns `None`; oversized bodies and unexpected acquisition errors propagate. |
| `llm.py` | `LLMClient` protocol + `InstrumentedLLMClient` (logging wrapper for OpenAI-compatible clients) |
| `llm_anthropic.py` | `AnthropicLLMClient`: native Anthropic messages-API client implementing the same `LLMClient` protocol (`.prompt` / `.pydantic_prompt`). Lazy `import anthropic`. Optional `thinking_budget` (set from the provider's `thinking_effort`) enables extended thinking — passes `thinking={type:enabled,budget_tokens}` and clamps `max_tokens` up to `budget+1024` (the API requires `max_tokens > budget`); `None` keeps thinking off. Temperature is never sent (Opus 4.x rejects it). |
| `llm_models.py` | List a provider's available model ids for the Settings picker: `list_openai_models` (httpx `GET {base_url}/models`) · `list_anthropic_models` (SDK `models.list`). Read-only; key passed in already resolved. |
| `openalex.py` · `openalex_cache.py` | prestige lookups + their SQLite cache. `OpenAlexClient(allow_network=False)` is cache-only (a miss → None, no HTTP) for interactive request paths that must not block on a search. An unresolved author list yields `max_author_h_index=None` (never a fabricated zero). For **cold-start** works (no own percentile) `_enrich_with_authors` also fetches each author's field-normalized standing (`max_author_field_percentile` = median of the author's works' `citation_normalized_percentile`) — skipped for established papers to bound the extra `/works` calls. `OpenAlexWork.abstract` exposes the abstract reconstructed (`_abstract_from_inverted_index`) from the work's `abstract_inverted_index` (already in the cached payload) — the gate's `_backfill_abstracts` uses it to recover title-only RSS items so they become scorable. `search_works(query, *, per_page, semantic)` is the Targeted Search candidate-generation leaf: `search`/`search.semantic` relevance ranking (citation-weighted lexical → a candidate-gen signal, NOT our relevance) with a bounded `select=`; returns `OpenAlexSearchHit` (id/title/abstract/doi/authors/venue/cited_by/oa/retracted/source_rank). Keyless polite pool has a tiny per-request budget, so it fills in behind arXiv+EuropePMC |
| `arxiv.py` | Keyless arXiv Atom search leaf for Targeted Search (`search_arxiv(query, *, max_results)` → `ArxivHit`). Host-locked to `export.arxiv.org` (SSRF guard), `RateLimiter(3)`, stdlib `ElementTree` parse of the Atom feed. Best-effort: HTTP/parse error → `[]` (documented leaf boundary, mirrors `unpaywall.py`) |
| `europepmc.py` | Keyless Europe PMC REST leaf. `search_europepmc(query, *, page_size)` → `EuropePmcHit` (`resultType=core`, carries the abstract). `fetch_fulltext_xml(pmcid)` → plain body text of a PMC open-access article via the REST `fullTextXML` endpoint, `<body>`-only JATS strip (drops journal metadata/refs, `html.unescape`) — the reliable machine-readable OA full-text path where the `fullTextUrlList` `?pdf=render` link 404s and NCBI's `/pdf/` mirror serves a bot-block page. Host-locked to `ebi.ac.uk`, `RateLimiter(5)`. Recovers biomedical/PubMed literature the arXiv/OpenAlex channels miss. Best-effort: HTTP/parse error → `[]` / `""` |
| `crossref.py` | Keyless Crossref works-search leaf for Targeted Search (`search_crossref(query, *, rows, mailto)` → `CrossrefHit`). Host-locked to `api.crossref.org` (SSRF guard), `RateLimiter(5)`, optional `mailto` → the faster polite pool. ~150M works across every publisher (broadest recall channel); every hit carries a DOI so it dedupes cleanly. JATS `<jats:p>` abstracts stripped to plain text. Best-effort: HTTP/parse error → `[]` |
| `semantic_scholar.py` | Keyless Semantic Scholar Graph paper-search leaf (`search_semantic_scholar(query, *, limit)` → `SemanticScholarHit`). Host-locked to `api.semanticscholar.org` (SSRF guard). Strong relevance ranking + abstracts; exposes DOI/ArXiv/PubMed ids for cross-source dedup. The keyless tier is heavily throttled (shared ~1 req/s, frequent 429s) so it's gated on a conservative `RateLimiter(1)` and a 429/any error → `[]` (a throttled source contributes nothing this run, never raises) |
| `openreview.py` | **Authenticated** OpenReview peer-review leaf for Targeted Search (`search_openreview(client, query, *, limit, with_ratings=False)` → `OpenReviewHit`). Anonymous access is walled (403 ChallengeRequired on structured `/notes`), so `OpenReviewClient` logs in with `OPENREVIEW_USERNAME`/`OPENREVIEW_PASSWORD` read from the ENV at point-of-use (never config, never logged) → JWT, re-login-once on a 401. Host-locked to `api2.openreview.net`, `RateLimiter(3)`. Returns only ACCEPTED papers at configured venues (host-substring match on `venueid`) in the year window, each with its main-conference `tier` (oral/spotlight/poster; **workshop is a separate tier detected from the `.../Workshop/...` venueid path**, since a workshop's own "Poster" label is not a main-conf prestige signal) + reviewer-rating aggregate (`mean_rating`/`n_reviews`, RAW per-venue scale — normalization needs the whole pool so it lives in the ranker). The per-paper `forum_reviews` GET that fills the rating is **opt-in via `with_ratings=True`** — production consumes only the venue+tier chip (`services/search/_relevance._peer_review_chip`), so the default skips the fan-out; the two OpenReview benches opt in to exercise the rating arms. Per-forum review aggregates cached in `OpenAlexCache` (`openreview:reviews:<id>`). Absent creds or `allow_network=False` → `enabled=False` → `[]`; any transport/parse error → `[]` (leaf boundary). The tier/rating signal drives **search ranking ONLY** — deliberately kept out of the triage citation-prestige blend (Leiden: venue prestige is gameable) |
| `unpaywall.py` | DOI → open-access PDF URL |
| `github.py` | `check_repo(owner, repo)` → `RepoCheck(exists, status, description, title)`. **Keyless, host-locked** repo validator for the deep-review code-link layer (`services.library._code_link`): scrapes the public `github.com/{owner}/{repo}` HTML page (NOT the REST API — its 60 req/hr unauth cap would exhaust a fleet run) so liveness (200 / 404→dead / error→`exists=None` unknown) AND the OpenGraph description/title come from ONE request. Host is hard-pinned to `github.com` (SSRF / indirect-prompt-injection guard — paper text can't steer the outbound request). Short timeout (`_DEFAULT_TIMEOUT`, `ZS_GITHUB_TIMEOUT` override). Best-effort: a transport error → `exists=None`, never raises (mirrors `unpaywall.py`) |
| `_rate_limiter.py` | `RateLimiter` — per-process token bucket (≤ rate calls/sec); the single shared limiter for `openalex.py` (10 req/s) and `pubmed.py` (3 req/s) |
| `pubmed.py` | PMID/DOI → PMC article PDF URL (keyless NCBI ID-Converter → PMCID; cached `pmc:<id>` in `OpenAlexCache`, 3 req/s). Recovers PubMed papers in PMC with **no DOI** (e.g. AMIA proceedings) that the DOI-keyed Unpaywall/OpenAlex rungs can't reach. Fresh PMC has no reliable headless download (bot-wall interstitial), so the URL is for `_pdf_acquire`'s **browser** rung; the headless attempt falls through. Best-effort: network/parse errors → `None` (mirrors `unpaywall.py`) |

**Boundaries:** must NOT import `services/` or `api/` (enforced by pre-commit).

RSS/PDF connections use a validated numeric IP, never a second hostname lookup.

Browser PDF acquisition and the headed login window use `_browser_network.public_browser_options`:
a session-scoped authenticated loopback HTTP/CONNECT proxy reuses the RSS/PDF
public-address resolver and connects only to its validated numeric destination.
Chromium and its context API requests use that proxy; there is no DIRECT fallback,
and Chromium's implicit localhost bypass, QUIC and non-proxied WebRTC UDP are
disabled. CONNECT preserves origin TLS and browser cookies. Metadata PDF links
are validated before navigation, including rejection of non-HTTP schemes.
Proxy worker errors are rethrown before publication; navigation/DOM/transport
errors no longer become an empty PDF result. This is an egress restriction, not
a browser sandbox or a decoded/rendered-body memory bound (A111 remains open).
Login navigation/close failures return an explicit failed result and never create
the completion marker. An existing marker is retained; it records a completed
flow, not proof that authentication is currently valid. Blank login URLs still
open a guarded window for manual navigation; non-public SSO destinations are blocked.
The proxy is session-local, authenticated with a random ephemeral secret, limited
to 32 concurrent connections and closes with the browser. It is not an app endpoint.
The normal suite checks socket framing/pinning and browser adapters; run
`ZS_BROWSER_EGRESS_SMOKE=1 KMP_DUPLICATE_LIB_OK=TRUE .venv/bin/pytest -q --forked tests/test_browser_egress_live.py`
for actual Chromium against synthetic origins (no internet or real browser profile).
Proxy routing follows [Playwright's supported options](https://playwright.dev/python/docs/network#http-proxy)
and [Chromium's explicit loopback-bypass override](https://chromium.googlesource.com/chromium/src/+/HEAD/net/docs/proxy.md#overriding-the-implicit-bypass-rules).

HTTP and browser PDF functions require an explicit `cache_dir`; their service
callers pass `Settings.pdf_cache_dir` (`data/pdfs/`). No import-time HOME path or
runtime/service lookup lives inside these integration leaves.

Both browser cache readers use `pdf_fetch.valid_pdf_path` with the caller's
`max_bytes`, like the HTTP cache: a nonempty file within the limit and `%PDF`
magic. This is a cheap cache-admission check, not full PDF structural validation.
Valid cache hits need neither a browser dependency nor network access. Invalid
entries are not returned as PDFs; they remain untouched until a successful
replacement, including when the browser is absent or acquisition raises.

Browser body reads request at most 64,000 bytes or the remaining limit plus one
detection byte; rejected chunks never enter the accumulated body and stream
handles close on success/error. Fetch interception covers the main document and
PDF responses at the header stage, before the driver can collect an entire body.
Non-PDF HTML is fulfilled with its decoded bytes so scripts and session cookies
still work. Print output uses CDP `ReturnAsStream`, not `page.pdf()`'s internal
unbounded concatenation. This bounds transferred/accumulated body bytes, not
Chromium's DOM, image-decoding or PDF-rendering working memory.
Both Fetch request and response stages are enabled: the request stage is needed
for authenticated-proxy challenges. Only the exact session proxy receives its
ephemeral credentials, once per request; origin/foreign/repeated challenges are
cancelled. No proxy credential is added to an article request's headers.

Both app-RSS and Zotero `get_feed_items(limit=None)` return all matching rows;
finite read limits retain their existing cap. Unlimited CLI runs pass None
explicitly, so neither the 1000-row default nor the 5000-row finite cap truncates them.
The original Host header and certificate name use HTTPX's documented
[`sni_hostname` extension](https://www.python-httpx.org/advanced/extensions/#sni_hostname).
Automatic clients disable idle connection reuse so unrelated hostnames sharing
an IP cannot reuse each other's TLS session. Every redirect is resolved afresh.
RSS requests negotiate identity/gzip and cap both wire and decoded bodies at
8 MiB; gzip output is bounded during decompression. Invalid/trailing gzip and
unnegotiated encodings are errors. Original XML bytes preserve declared encodings.

Item lists, details, notes and annotations exclude trashed children (including
annotations on trashed PDFs); note lists/detail share one query. Collection lookup
belongs to the collection mixin and scopes key/name/every path segment to the user
library. An explicit foreign key never falls through to another name. Requested
materialization collections must resolve or the transaction fails.
Feed materialization acquires `BEGIN IMMEDIATE` before its existing-key probe
and writes all item/collection/tag/note steps in that transaction. Replaying a
committed user-library key is a no-op for the whole operation, preserving later
user edits; callers must reuse a reserved key only for the same creation intent.
Queued feed-read writes verify both item and feed-library IDs and preserve an
existing read timestamp. Note insertion deduplicates identical visible HTML;
upsert requires a marker in its HTML and matches it literally among live notes.
Tag removal deletes all matching case variants linked to the selected item,
using the existing SQLite `lower` matching semantics. It does not delete the
shared tag records or links belonging to other items. A one-row lookup is still
appropriate for tag creation/reuse, but cannot implement complete removal.
Adapters are leaves — they depend only on stdlib, third-party clients, and
`models`/`domain`.

Stored-attachment names strip separators and non-printable Unicode characters.
Known PDF/PNG extensions are normalized and reserved before the 120-character
limit; empty, `.` and `..` stems use `fulltext` plus the content-type extension.
The same sanitized name is used for the copied file and SQLite `storage:` path.
