# integrations — external-system adapters

Contract for changing this package: thin I/O clients for everything outside the app (no business logic).
Product behavior: [docs/features.md](../../docs/features.md); placement: [docs/architecture.md](../../docs/architecture.md).

## Boundaries

- Leaves depend only on stdlib, third-party clients and `models`/`domain`; they never import `services/` or `api/`
  (enforced by pre-commit). Persisted receipt policy is service-owned.
- HTTP and browser PDF functions take an explicit `cache_dir` (callers pass `Settings.pdf_cache_dir`); no import-time
  HOME path or service lookup lives here.
- Best-effort scholarly leaves return `[]`/`None` on transport or parse error; unexpected runtime failures propagate.
  Hosts are locked per adapter (SSRF guard) and rate-limited by `_rate_limiter.RateLimiter`.

## Files

| file | responsibility |
|---|---|
| `zotero_read.py` | `ZoteroReader`: read-only WAL-aware connections, bounded lock retries (persistent lock raises), collection/stat helpers scoped to the user library, trashed items/attachments excluded; `collection_name_for_key` validates picker keys; Inbox and read-next sort first |
| `_zotero_read_items.py` · `_zotero_read_lookup.py` · `_zotero_read_feeds.py` | reader mixins: items/detail/notes/annotations (`item_type`, `venue`; `get_items` paged 500, `get_all_items` one unpaged query) · find/membership/tags (DOI match over all `normalize_doi` variants; `get_pdf_paths(keys)` = each key's `get_item_detail` `pdf_path` over one connection, without fields/notes/annotations) · feeds (`get_unread_feed_guid_map`; `get_feed_items(limit=None)` returns all rows) |
| `_zotero_read_common.py` | `ZoteroReadError`, arXiv/sanitize helpers, `_NON_BIBLIOGRAPHIC_TYPES_SQL`, `_USER_LIBRARY_ID_SELECT` (leaf) |
| `zotero_write.py` | `ZoteroWriter`: WAL-consistent backup, `apply_changes` dispatcher, `_prune_backups` |
| `_zotero_write_items.py` · `_zotero_write_feed.py` · `_zotero_write_fields.py` · `_zotero_write_attachments.py` · `_zotero_write_tags.py` · `_zotero_write_collections.py` | writer mixins: item creation/feed materialization · feed-read bookkeeping · single-field upsert · imported_url attachments · tag/note appliers · collections |
| `_zotero_write_common.py` | `ZoteroWriteError`, `resolve_user_library_item_id` (user-library item resolution; `required=False` for best-effort batch removes) |
| `app_rss.py` · `rss_paging.py` | `AppRssReader`: app-owned RSS items, rotating bounded refresh, `stream_public_url` (shared with PDF fetch). **No entry is sliced before dedupe** (REQ-S4): every entry is upserted; arXiv `api/query` URLs sorted `submittedDate desc` are paged via `start` (≥3 s apart per arXiv terms) until `max(feed watermark, now − feeds.backfill_days)` or `feeds.max_pages_per_feed`. The per-feed `fetch_watermark` advances only on a confirmed boundary; a cut-off sets `fetch_incomplete` (Settings shows “Incomplete fetch”) and the next refresh re-reads the tail |
| `pdf.py` · `pdf_fetch.py` | local PDF → analysis text via OnPrem `pdf_markdown=False`; OA PDF download with size/timeout/magic caps; `valid_pdf_path` cache admission; `biorxiv_pdf_url` maps a bioRxiv/medRxiv `/content/` landing to `<landing>.full.pdf` (RSS mangles the preprint DOI, so Unpaywall/OpenAlex miss it) |
| `browser_fetch.py` | institutional PDF acquisition with Patchright/Playwright Chromium and a persistent login profile; `article_snapshot_path` |
| `_browser_network.py` · `_browser_response.py` · `_browser_article.py` · `_article_pdf.py` | public-only loopback egress proxy · bounded CDP response capture + proxy auth · isolated-world `document.body` text collection · bounded text-only PDF writer (PyMuPDF Helvetica/`cjk`) |
| `llm.py` · `llm_callbacks.py` · `llm_models.py` | `LLMClient` protocol + `InstrumentedLLMClient` (logging, capability-gated JSON Schema via `build_response_format`; `with_larger_output()` → a sibling client with a doubled reply budget ≤ `OUTPUT_RETRY_CEILING`, used once by the review pipeline after `finish_reason=length`; none for verification clients) · completion-metadata guard that raises `LLMOutputTruncated` on `length`; `llm_models.py` lists provider models for the Settings picker (read-only, key already resolved) |
| `llm_anthropic.py` · `_anthropic_http.py` | native Anthropic client (lazy `import anthropic`; `thinking_budget` ⇒ `thinking` + `max_tokens ≥ budget+1024`; temperature never sent) · no-auth native HTTP helpers |
| `llm_transport.py` · `llm_requests.py` | request-owned destination/auth/headers for catalog and no-auth clients · explicit `RequestInventory` for verification calls |
| `jev.py` | Typed-decision (Noul/Choice) wire client: `ask(target, state, questions)` → `{id: raw answer}` / `noul(...)` → `{id: P(yes)}`, `None` on any failure (never a 'no'); `Target(engine, url, key)` is passed in — Cloud default `CLOUD_URL`, Local = any Jev-compatible server (`LOCAL_URL`). Which target to use is decided by `services/llm/jev_engine.py` (Settings → Checklist engine); the key is never logged |
| `ollama_runtime.py` · `_ollama_store.py` · `_process_guard.py` | isolated native runtime from installed weights · read-only manifest/blob snapshot into a private store · finite owned-process guard |
| `ollama_inventory.py` | read-only `/api/tags` size + `/api/ps` residency of an Ollama daemon, and a best-effort `keep_alive: 0` unload (memory-checked local Test) |
| `openalex.py` · `openalex_cache.py` · `_rate_limiter.py` | prestige lookups (`OpenAlexClient(allow_network=False)` is cache-only) and `search_works` · SQLite cache shared with Unpaywall/PubMed/OpenReview; `_rate_limiter.RateLimiter` is the shared per-process token bucket |
| `arxiv.py` · `europepmc.py` · `crossref.py` · `semantic_scholar.py` · `_search_http.py` | keyless search leaves (`ArxivHit`, `EuropePmcHit` + `fetch_fulltext_xml`, `CrossrefHit` with `mailto`, `SemanticScholarHit`) · shared request/owned-client lifecycle |
| `openreview.py` | authenticated OpenReview leaf (`search_openreview` → `OpenReviewHit`; optional `outcome` receipt in the shape of the other search leaves) |
| `unpaywall.py` · `pubmed.py` · `github.py` | DOI → OA PDF URL · PMID/DOI → PMC PDF URL (browser rung) · `check_repo` scrapes the public HTML page (not the REST API); transport error ⇒ `exists=None` |
| `telegram.py` · `telegram_threads.py` · `telegram_reply_adapter.py` · `telegram_session.py` · `telegram_child.py` · `telegram_auth.py` | injected Telethon page client · bounded read-only conversation collector · thin adapter exposing the conversation fetch as the one-pipeline digest `reply_client.get_replies` seam (adds no capability; live-only) · dedicated macOS CLI session lifetime · `run_child` spawns the one-shot auth/digest child and verifies its receipt (services and cli import it from here) · explicit auth callback |

## Zotero reads

- Every whole-library read injects `_USER_LIBRARY_ID_SELECT` (`type='user'`): Zotero keeps feed libraries in the same
  `items` table, so an unscoped read leaks feed items into corpus, ranker, tag writes and full text.
- Library lists exclude attachments, notes and annotations (`_NON_BIBLIOGRAPHIC_TYPES_SQL`); lists, details, notes and
  annotations exclude trashed children, including annotations on trashed PDFs.
- Item detail projects annotation key, parent attachment key and `dateModified`; IDs are never invented from position.
- Collection lookup scopes key, name and every path segment to the user library; a foreign key never falls through to
  another name. Requested materialization collections must resolve or the transaction fails.

## Zotero writes

- Every write that targets an item by key resolves through `resolve_user_library_item_id`, so a feed item's key is
  never mutated or parented into the user library.
- `apply_changes`: backup first; one explicit transaction encloses per-row savepoints; a lock error rolls the whole
  batch back before retry; invalid rows return individual failures; unexpected errors propagate; WAL setup failures
  are not swallowed; backups prune only after a successful write (`_BACKUP_KEEP`, app-pattern files only, never
  Zotero's own).
- `mark_feed_items_read(list[int]) -> int`: nonblocking per-library file lock through commit and pruning; more than
  five retained app backups refuses before any backup/native effect (a failure never prunes — the operator leaves at
  most five and retries); backup precedes the native retry; success prunes. Empty input does nothing. Not
  backup-to-write isolation from other native writers and not rollback-on-every-error. Queued feed-read writes verify
  item and feed-library IDs and keep an existing read timestamp.
- Feed materialization takes `BEGIN IMMEDIATE` before its existing-key probe and writes item/collection/tag/note in
  that transaction. Replaying a committed user-library key is a whole-operation no-op (later user edits survive);
  callers reuse a reserved key only for the same creation intent. `existing_only=True` opens `mode=ro` + `query_only`,
  acknowledges only that user-library key (missing → `None`), and bypasses backup, pruning, mutating PRAGMAs and
  materialization; it works while Zotero runs and does not certify an earlier raised attempt or live membership.
- Notes: insertion dedups identical visible HTML; upsert matches a marker literally among live notes. Notes use
  Zotero's `<div class="zotero-note znv1">` envelope (existing envelopes unchanged; legacy fragments replay
  idempotently; user notes are never migrated).
- Ownership is structural, never a substring search: the final meaningful paragraph's ` · `-separated metadata suffix
  wins; malformed/duplicate fields fail safe; escaped comments and code/quote blocks never establish ownership;
  leading legacy HTML comments are honored; a comment-free native note is unowned. Triage metadata needs version,
  generated_at and source; verdict/digest/user_note keep their version-1 two-field protocol.
  Legacy exception: a note whose whole app-rendered markup (marker comment + `<h2>…`) was stored HTML-escaped inside one
  `<p>` (older writer bug, shows literal `&lt;!-- …`) is owned only when that text is exactly such a rendering; extra
  paragraphs/markup (user edits) or a lone escaped comment stay unowned.
- `upsert_note` rewrites the first owned note (legacy escaped included) in place with the new body; any further owned
  notes with the same marker are duplicates and go to the Zotero trash (`deletedItems`, restorable) — one note per marker.
- Tag removal deletes every case variant (SQLite `lower`) linked to the selected item and leaves shared tag rows and
  other items' links.
- Stored-attachment names strip separators and non-printable characters; known PDF/PNG extensions are reserved before
  the 120-char limit; empty/`.`/`..` stems become `fulltext` + the content-type extension; the copied file and the
  SQLite `storage:` path use the same name.

## Network egress, RSS and PDFs

- RSS and PDF connections use a validated numeric IP resolved once; the original Host header and certificate name use
  HTTPX's `sni_hostname`. Idle connection reuse is off, every redirect re-resolves, proxy env is ignored.
- RSS bodies are identity/gzip only, capped at 8 MiB on the wire and decoded (gzip bounded during decompression);
  invalid/trailing gzip and unnegotiated encodings are errors. Only a typed SAX/Expat unbound-prefix warning may use
  namespace compatibility (a DOCTYPE-forbidding namespace-neutral parse, then an in-memory namespace-aware check);
  extraction always consumes the original feedparser result. Other syntax/encoding/depth failures are typed source
  rejections; storage/programming `ValueError` aborts.
- `refresh_feeds`: offline returns before network/DB writes; each feed commits independently; HTTP/DNS/document
  failures are recorded and the rest continue (partial → `errors`; all failed → last exception propagates;
  storage/programming errors abort with rollback). Counts are committed items only; text is sanitized before DOI/key
  derivation. `refresh_feeds` counts ITEMS as `inserted`/`updated`/`unchanged` (`feeds` is the number of feeds fetched; `storage.rss.upsert_rss_item` rewrites an existing row only when its content differs and returns `inserted|updated|unchanged`, so a re-fetched identical item counts unchanged). An item keeps its first feed, so one story served by several feeds never ping-pongs; `_entry_to_item` strips the expiring signed query of `private-user-images.githubusercontent.com` URLs from abstracts (GitHub release notes) so they do not differ per fetch. `last_error` may carry the `RssNamespaceWarning:` marker, cleared by a clean refresh; a warning or
  compatibility never certifies source meaning.
- `get_feed_items(limit=None)` (app RSS and Zotero) returns all rows; finite limits keep their cap.
- `pdf_fetch` streams with a size cap, timeout and `%PDF` magic, sending the browser-like `github.BROWSER_UA` (publishers such as nature.com serve HTML to the default client UA; RSS fetching keeps its own UA); SSRF/redirect guards are unchanged. Cache admission (`valid_pdf_path`, shared by HTTP and
  browser readers) is nonempty, within `max_bytes`, `%PDF` magic — not structural validation. Valid hits need neither
  browser nor network; invalid entries are never returned and stay untouched until a successful replacement.
- Browser egress (`_browser_network.public_browser_options`): a session-scoped loopback HTTP/CONNECT proxy,
  authenticated with a random ephemeral secret, reuses the public-address resolver and connects only to the validated
  numeric destination (≤32 concurrent connections; a burst beyond that queues ≤15 s for a free slot, a still-unserved
  connection is dropped and retried by the browser, never failing the session; a connection reset or broken pipe on a tunnel is not a session error (any other handler error, e.g. a refused non-public destination, still raises at session exit); closes with the browser; not an app endpoint). No DIRECT fallback; implicit
  localhost bypass, QUIC and non-proxied WebRTC UDP are disabled; CONNECT preserves origin TLS and cookies. Metadata
  PDF links are validated before navigation (non-HTTP schemes rejected). An unresolved proxy hostname returns 502 for
  that request; an initial article DNS failure raises; non-public addresses stay blocked. Only the exact session proxy
  receives the credential, once per request; other challenges are cancelled. This is egress restriction, not a sandbox
  or RAM limit.
- Browser reads: chunks ≤64,000 bytes or remaining limit plus one detection byte. A captured PDF needs real `%PDF-`
  bytes within `max_bytes` regardless of MIME; a typed missing-PDF status needs a main-frame `application/pdf`
  response with HTTP 400+ (not 407). A declared-but-unavailable scholarly PDF never becomes a rendered paywall stub.
  Missing dependency/non-PDF → `None`; oversized bodies and unexpected acquisition errors propagate.
- HTML-only articles: the main-document response is capped before fulfillment; `_browser_article` walks
  `document.body` text nodes in a CDP isolated world (≤8 KiB chunks; byte/node/deadline limits) and fails closed on
  mutation, partial traversal, invalid/empty text or exhausted limits (never a prefix). `_article_pdf` writes a
  fixed-layout text-only PDF with source/page/output caps and a text round-trip check; `Page.printToPDF` is never
  used. These caps do not bound Chromium's own memory or subresource traffic.
  Text the `cjk` font cannot draw is normalised first, not rejected: space separators (NBSP, thin space, …) → a plain
  space, invisible format characters (soft hyphen, zero-width space, BOM) dropped, compatibility forms via NFKC, minus
  sign → `-`; a character with no such form (e.g. emoji) still fails loudly.
- One browser session at a time (`_BROWSER_LOCK`, profile singleton + RAM): a concurrent fetch/render waits up to 180 s
  for it instead of skipping its PDF source; the interactive login flow still refuses when busy.
- Snapshot cache keys: derivatives use `cache_dir/article-snapshots/<full SHA-256(raw URL)>.pdf`, reused only with
  `render_fallback=True` after the source-PDF cache check; captured publisher PDFs use `cache_dir/<first 16 hex
  SHA-256(raw URL)>.pdf`. See [reading source boundary](../../docs/features.md#reading-reviews).
- Headed login: navigation/close failures return an explicit failed result and never create the completion marker (an
  existing marker records a completed flow, not valid authentication); non-public SSO destinations are blocked. Egress
  smoke: `ZS_BROWSER_EGRESS_SMOKE=1 KMP_DUPLICATE_LIB_OK=TRUE .venv/bin/pytest -q --forked
  tests/test_browser_egress_live.py`.

## PDF analysis text

- `pdf.py` loads analysis text with `load_single_document(path, pdf_markdown=False)` (Markdown conversion interleaves
  signs and super/subscripts); presentation keeps its own parser. Source-length budgets use the extracted length.
- Triage's `capture_pdf_source` hashes in 64 KiB reads before and after extraction/annotation reading; changed
  identity/content, malformed SDK data and read/hash/extraction failures raise before corpus/model work (ordinary
  changes only, not a hostile writer restoring identical bytes). SDK annotation URI tuples keep order and duplicates
  verbatim; abstract-only calls never import the PDF reader or probe a path.

## LLM adapters

- `CompletionGuard` logs usage/finish reason without response text; `length` raises `LLMOutputTruncated` before
  parsing. `observe_completions` is an explicit invocation-scoped observer that runs before parsing/truncation
  rejection (a typed SDK length error is normalized from its decoded single completion, empty content, explicit
  provenance). No observer ⇒ no retention; missing usage/model metadata is unknown; observed model names do not pin
  mutable remote weights.
- Setup verification (`RequestInventory` + the owned HTTPX pre-send hook) needs observed exact model identity and a
  successful terminal reason; missing observations, truncation, model mismatch and cached text cannot verify. Cache,
  retries and redirects are off, cancellation is checked before every request, owned transport closes, and paper text
  never controls these options. Pre-send entry is ownership evidence, not remote acceptance or termination proof.
- `llm_transport`: raw proxy-prefix admission compares authorities through the HTTPX URL parser (case/default port
  equivalent; origin, port, path or userinfo changes rejected); no ambient proxies/redirects. Owned headers are
  reconstructed, not allowlisted, so poisoned User-Agent, `anthropic-beta`/`version` and `x-stainless-*` values cannot
  survive; per-request `Omit` is required (constructor defaults are insufficient). Helpers require an explicit
  timeout; OpenAI's webhook secret is explicit-empty.
- The synchronous OnPrem owner must keep `x-stainless-raw-response: true`: LangChain needs the response wrapper, not a
  parsed object (`root_client` for the schema path, `client` for raw Chat, one owned SDK root).
- Native Anthropic no-auth skips the SDK constructor's ambient active-config lookup; `_anthropic_http.py` reuses pure
  SDK schema/parse helpers. A null native base is the official API. Requires Anthropic ≥0.105.2 and OpenAI ≥2.36.0;
  parsing uses the pinned private `anthropic.lib._parse._response.parse_response` with no import fallback.

## Native runtime (`ollama_runtime.py`)

- Prepares an isolated store from installed manifests and weights; never pulls and never modifies the user's daemon or
  store. Weights are hard-linked on one volume; only a cross-device link error selects an absolute symlink (external
  weights need their disk connected); missing sources, changed sizes and other link errors refuse; cleanup leaves
  sources untouched. A private `num_batch` manifest (default 128) is the only change; blobs and sampling parameters
  stay unchanged.
- `_process_guard` owns the evaluator/daemon/runner group: available-RAM floor 20%, swap growth ≤2 GiB, combined RSS 4
  GiB by default; one finite run may be scoped with a positive integral `--rss-mib` that the runtime must also accept
  via `rss_limit_mib` (a guard receipt alone never raises admission). A pipe sentinel reaps on monitor death or
  command exit.
- Admission needs native observations, not declared options: `/api/ps` digest, effective context and placement joined
  to the owned listener/runner; matching `-b`/`-ub`; allocated K/V types (`f16`/`q8_0`/`q5_0`/`q4_0`, agreeing with
  worker flags; defaults need both K and V facts); disabled host prompt-state cache and context checkpoints
  (`LLAMA_ARG_CACHE_RAM=0`, `LLAMA_ARG_CTX_CHECKPOINTS=0` plus the log observation). Only a proven CPU/host or Apple
  Silicon unified domain may use fresh OS availability; missing/foreign evidence refuses. Load/unload cannot certify
  inference. Declared `thinking_effort`/`max_tokens` are configuration, not proof. No ambient environment passthrough.

## Search leaves

- Opt-in `outcome` receipts (OpenAlex, arXiv, Crossref, Europe PMC, Semantic Scholar): status, returned count (never
  query, URL, body or exception text), bounded error code. Observed empty ⇒ count 0; failed/unknown ⇒ no count; blank
  input does no HTTP. Malformed containers/rows and nonfinite OpenAlex JSON are parse failures; neighboring requests
  stay usable; caller-owned clients stay open; ordinary calls return plain lists. `europepmc.fetch_fulltext_xml(pmcid,
  outcome=None)` clears its outcome per call and records only the public XML endpoint as `source_uri` on nonempty
  success, never a local path.
- OpenAlex keeps DOI/arXiv aliases from `locations` in `family_identifier_keys` (URLs parsed, never followed);
  unresolved authors give `max_author_h_index=None`. Enrichment visits the first `max_authors` (3) **plus the last author** of any list; `OpenAlexWork.last_author = {name, h_index}` is the last author's OWN h (`None` = unknown, a real 0 stays 0), flowing to `aux_context.last_author_h_index` and the Today card. Search keeps Europe PMC `pubTypeList.pubType` and OpenAlex `type`
  as publication-type lists (unknown → empty).
- OpenReview credentials come from `OPENREVIEW_USERNAME`/`OPENREVIEW_PASSWORD` at point of use (never config, never
  logged; re-login once on 401); none or `allow_network=False` ⇒ `[]`. Per-paper rating fan-out is opt-in
  (`with_ratings=True`). Its signal drives Search chips only, never the triage prestige blend.

## Telegram

- `telegram.py` is an injected Telethon leaf with no session, lifecycle, retry or store ownership. It emits schema-v2 posts with `replies_count` (from `MessageReplies`), `fwd_from` (a forwarded channel post) and `reply_to_id` (a post replying to a post of the same channel; a reply into another chat is None), and `telegram_threads` stores the numeric `sender_id` of each comment (no names), and gives schemeless auto-linked domains (`Z.ai`) an `http://` prefix so one such post cannot void a whole page. The reply adapter passes a `TelegramPost` origin (`telegram_threads._post_origin`).
  `telegram_threads.py` reuses it for public broadcasts, megagroups and forum roots: `GetDiscussionMessage` routes
  linked replies with origin consistency, `GetReplies` keeps nested parent/top IDs, one bounded newest window (≤300 replies, paged 100 per `GetReplies`) is
  refetched per run, and peer/order/route are validated before originals are retained. Reply-peer scope is a boolean,
  never a user identity. A later-post stop keeps completed observations plus origin-bound unavailable/not-attempted
  rows and makes no further RPCs. No join/send/delete; the dedicated session owner connects/disconnects.
