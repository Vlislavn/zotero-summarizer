# `services/card/` — Today card (facts + AI)

Computes the facts the owner decides on, for ONE paper, by **reusing existing
machinery** — no new DB table, no migration. The AI card (Todo #92) adds grounded
model fields on top of those facts, gated by the existing processing-boundary
consent and served over a small API.

```
read_card(key)      -> cached AI card OR facts-only (ai_available:false + reason); never calls the model
generate_card(key)  -> facts + AI fields over the `card` stage, consent + spend-guarded, idempotent
background.start()  -> Today's top-15 + the unreviewed shelf, <=4 in flight, via the feed tick; never blocks a request
cached_bullets(keys)-> {key: TL;DR bullets} for fresh cached cards only (no facts, no model); feeds the Today shelf
```

## AI card (card.py, _ai.py, _consent.py, _store.py, background.py)

- **Fields** (`_ai.py`, terse TL;DR #127; one grounded structured call over the `card`
  stage): the model returns `bullets` — at most 3, labelled `New` (what is actually
  new), `Take` (ONE reusable method/protocol/prompt/eval, with a verbatim evidence
  quote) and `Caveat` (overclaim / missing baseline / weak eval: a verbatim quote of
  the paper's OWN limitation, or an `absence` basis the deterministic facts confirm AND
  that matters for this paper) — each a telegraphic English fragment of ≤12 words,
  ≤45 words in all, dropped rather than padded. The app adds a 4th `Code` bullet from
  the facts (never from the model; nothing when no full text was read). Output is
  `ai: {bullets: [{label, text}]}` + `ai_status` per label (`ok`/`dropped`/`missing`);
  a `Take`/`Caveat` bullet that fails its grounding is dropped. Quotes use the shared
  fuzzy grounding contract; the method input is capped at 50k chars. Eval protocol =
  how THE PAPER evaluates; judge validation counts only vs humans — both from the facts.
  `PROMPT_VERSION=card-ai-2` + `CARD_VERSION=card-v5-1` + the new schema change the
  cache identity, so old prose (`card-ai-1`) cards are never read as current.
- **Consent** (`_consent.py`): reuses `ResolvedStage.fingerprint()` — the same
  processing-boundary token the review stages use. `record_consent(token)` persists the
  owner's acceptance of the current `card` boundary; `has_consent()` compares against the
  LIVE boundary, so any model/option change re-gates. Without consent / AI disabled /
  offline → facts-only card with `ai_available:false` and a reason.
- **Storage + guards** (`_store.py`): one JSON per paper under `Settings.card_dir`, keyed
  by stable key + card_version + prompt hash + model (a prompt/model change misses the
  cache). Per-field status (ok/failed/missing). Spend guards: per-paper call cap
  (`ZS_CARD_CALL_CAP`), daily token ledger (`ZS_CARD_DAILY_TOKENS`), circuit breaker
  (`ZS_CARD_BREAKER_N` consecutive failures → facts-only for `ZS_CARD_BREAKER_MINUTES`).
- **Stage**: a new `card` routing stage (`ROUTABLE_STAGES`, NOT user-facing `STAGES`);
  default provider Kather `sota` with thinking `off` in the config. Card knobs are
  operational — deliberately OUTSIDE the deep-review identity allowlist (#88).
- **API** (`api/routes/_card.py`): `GET /api/card/{key}` (cached or facts-only, never
  generates synchronously beyond facts) and `POST /api/card/{key}/generate` (single
  paper, idempotent, consent + caps). `key` is the stable/feed key.

## Facts (facts.py)

Computes the facts, by **reusing existing machinery** — no new DB table, no migration,
no LLM, no UI.

```
collect_facts(PaperRef)                      cache: Settings.card_facts_dir/<stable_key>.json
      │                                       (source_sha · extractor_version · per-fact fetched_at/ttl)
      ├─ _fulltext  ── library._pdf_acquire (arXiv→Unpaywall→PMC→OpenAlex→bioRxiv)
      │                 + library._paper_read_pdf (fitz text + PDF link annotations)
      ├─ _code      ── library._code_link.find_code_link (+ PDF-annotation URLs)
      │                 + third-party "released implementation" guard → own_live/promised/unclear/none
      │                 (no full text obtained → `unknown`, not a proven `none`)
      ├─ _published ── regex "what's published": prompts · eval protocol · judge-vs-human
      │                 validation WITH numbers · agent contracts · data  +  method checklist
      └─ _authors   ── PDF header affiliations (boilerplate stripped) + OpenAlex h-index
```

## Contract

- `collect_facts(ref, *, abstract="", offline=None, force=False) -> dict` — collect
  or re-read the cached facts. Compute **on demand only** (Today top-K ≈20, paper
  open). The second call for a live paper returns from cache with **no network and
  no PDF read**.
- `resolve_ref(key, …) -> PaperRef` — build a ref, filling identity/fetch fields from
  the feed row through the existing `feed_key` alias resolver, and from a bare identity
  key (`arxiv:<id>` → arXiv abs URL, `doi:<id>` → DOI) so the acquisition chain has a
  fetchable target even with no feed row. Explicit fields win.
- `PaperRef.stable_key` — `arxiv:<id>` → `doi:<norm>` → `key:<key>`. Keying by
  arXiv/DOI identity is the alias handling: a feed-key and the Zotero-key for the
  same paper map to one cache entry.

## Cache & freshness

JSON per stable key under `Settings.card_facts_dir` (`data/card_facts/`, gitignored).
Each fact group carries `fetched_at` + `ttl`: **liveness 7 d** (code repo
reachability), **artifacts 30 d** (text source, published chips, checklist),
**h-index 90 d**. The cheap re-read is gated on the shortest TTL (liveness), so it
never re-fetches; `force=True` recomputes. `source_sha` is the sha256 of the
extracted text (content-change key); `extractor_version` invalidates on logic change.

## Network budget & failure

Full text and OpenAlex reuse the app's cached clients. GitHub liveness goes through
`integrations.github` (keyless HTML page, not the metered 60/h REST API) via
`_code_link`, which caps at ≤3 live probes per paper. Every step fails soft: a dead
source degrades to abstract-only facts / `status="none"` / `max_h_index=None`, never
an exception. Honour `settings.offline_requested()` (passed through to
`find_code_link`).

## Boundaries

`api → services → storage/integrations → models`. This package imports
`services.library` helpers, `integrations.github`/`openalex`, `storage.feeds_lookup`
(alias resolver) and `api.errors` only. It writes nothing to Zotero and holds no
Settings paths of its own beyond `card_facts_dir`.

## Not here

No AI calls (the AI card is a later worker, same subpackage), no new SQLite table
or migration, no HTTP route / UI. Adding tests is owner-gated.
