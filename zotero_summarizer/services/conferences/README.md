# services/conferences — conference lists, kept apart

Owner-defined conferences whose abstracts live **only** under `Settings.conferences_dir`
(`data/conferences/`). Nothing here scores, embeds, clusters or calls a model; no other
domain (triage, Today, ranking, Library, Search, corpus) reads or receives these records
(REQ-S36). Layering: imports `api.errors`, `settings` and `services._common` only.

| File | Responsibility |
|---|---|
| `config.py` | `load_config(path)` — parses the owner's `Settings.conferences_config_path` (`data/conferences/config.yaml`, outside `goals.yaml`/`GoalsConfig`) on every call; each block is checked alone and a bad one yields `ConfigError(line, message)` while the others load |
| `catalogue.py` | `RECIPES` (only `asco`: Crossref ISSN, DOI pattern giving the abstract number, level rules, preflight rate) and `classify(recipe, number)` → `(level, why)` |
| `crossref_supplement.py` | `download` — cursor-pages `integrations.crossref.list_crossref_works` over the edition window, keeps rows whose DOI matches the recipe, never raises (returns the failure and the records received); `estimate_requests` (window days × records/day ÷ 1 000, capped at `PAGE_CAP`) |
| `store.py` | `<id>/<year>.json` (complete), `.json.prev` (the file before the last replacement), `.partial.json` (interrupted download); atomic writes; one in-memory parse per file stamp |
| `service.py` | `overview`, `preflight` (no network), `refresh` (the only network call), `edition_view` (late-breaking rows + title-sorted page of all rows, no abstract text), `abstract_of` |

## Invariants

- **Network only in `refresh`**, reached by the owner's POST after the preflight number is
  shown. Offline mode refuses it (`strict_offline`, «Offline mode is on»); a second refresh
  while one runs gets 409 `refresh_in_progress`. The Crossref polite-pool `mailto` is `prestige.user_agent_email` when set.
- **The complete file is replaced only by a complete download.** A failure (429, transport,
  HTTP error, unreadable response, request cap) keeps it, parks the records received in
  `.partial.json` and reports `ok: false` with the page, the reason and a retry time when the
  server gave one. An empty result is «No papers found»; nothing is written.
- **Levels are derived at read time** from the stored number and the recipe's rules, so a rule
  change needs no new download. A row no rule matches is `unknown` («no rule matched») — never
  guessed as «poster». ASCO: `^LBA` on the abstract number (the DOI suffix after `_suppl.`) is
  `late-breaking`; everything else is `unknown`.
- `data/conferences/config.yaml` holds `conferences: [{id, recipe, editions: {<year>: {from, until}}}]`;
  `id` becomes a directory name (lowercase letters, digits, `-`). No secrets are read or stored.

## Extending

A new conference of a known type is a block in the config. A new type is a recipe in
`catalogue.py` plus a downloader beside `crossref_supplement.py`; ESMO, AACR, OpenReview and
file import are not built yet.
