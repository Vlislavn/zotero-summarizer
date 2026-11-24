# services/conferences — conference lists, kept apart

Owner-defined conferences whose abstracts live **only** under `Settings.conferences_dir`
(`data/conferences/`). Nothing here scores, embeds, clusters or calls a model; no other
domain (triage, Today, ranking, Library, Search, corpus) reads or receives these records
(REQ-S36). Layering: imports `api.errors`, `settings`, `services._common` and `services._io` only.

| File | Responsibility |
|---|---|
| `config.py` | `load_config(path)` — parses the owner's `Settings.conferences_config_path` (`data/conferences/config.yaml`, outside `goals.yaml`/`GoalsConfig`) on every call; each block is checked alone and a bad one yields `ConfigError(line, message)` while the others load |
| `catalogue.py` | `RECIPES` (`asco`, `aacr`, `esmo`, `esmo_ai`: Crossref ISSN, how a row of that journal is recognised as a meeting abstract and where its number comes from, level rules, `abstract_available`, preflight rate), `abstract_number(recipe, hit)`, `shown_title`, `classify(recipe, number)` → `(level, why)` |
| `crossref_supplement.py` | `download` — cursor-pages `integrations.crossref.list_crossref_works` over the edition window, keeps rows `abstract_number` accepts (and, when the edition sets `volume`, only that journal volume), never raises (returns the failure and the records received); `estimate_requests` (window days × records/day ÷ 1 000, capped at `PAGE_CAP`) |
| `store.py` | `<id>/<year>.json` (complete), `.json.prev` (the file before the last replacement), `.partial.json` (interrupted download); atomic writes; one in-memory parse per file stamp |
| `table_reader.py` | `read_rows(name, bytes)` — the owner's CSV or XLSX (stdlib `csv`, `zipfile` + XML; first sheet) as rows of `number, title, authors, abstract, doi, session`; headers matched by alias, so `Poster ID`, `Abstract #` and `abstract_number` all are the number |
| `file_import.py` | `merge_rows` — merges those rows into an edition's records (no network); the rules are under Invariants |
| `service.py` | `overview` (each edition carries `abstract_available`), `preflight` (no network), `refresh` (the only network call), `import_file`, `edition_view` (late-breaking rows + title-sorted page of all rows, no abstract text), `abstract_of` |

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
- **Recipes.** `asco`, `aacr`: the DOI carries the number (`…_suppl.LBA1`, `1538-7445.am2026-lb333`) and Crossref has the
  abstract text; AACR's `Abstract 5512:` title prefix is not shown. `esmo`, `esmo_ai`: **title only** (`abstract_available`
  false, the UI says «title only»): a row is an abstract when its title opens with a number (`5P`, `12MO`, `2613O`, `LBA1`) —
  for `esmo` also on a Crossref page `S…` — and it does not say it «has been removed because it was not presented» (counted as
  skipped). ESMO is dated by print month in Crossref (2025: `2025-09`), so a window covers whole months. Only `LBA`/`lb`
  numbers are late-breaking; no other level is guessed.
- **Import** (`POST …/import?name=file.csv|xlsx`, the file as the request body; no network, allowed offline) merges the owner's
  file into the stored edition, or creates it when none exists. A row finds its record by abstract number (case, spaces and a
  spreadsheet's `8114.0` ignored), a row without a number by normalized title (the source's number prefix ignored); no match adds
  a record, a row with neither a title nor a match is skipped. Text from the file wins when it is not empty; authors and DOI only
  fill gaps; authors split on `;`. Records the file touched carry `imported: true`, and `refresh` applies them again to the new
  download, so an Update never loses an import. The same file twice changes nothing. A row without a DOI has the id
  `file:<number>`. `abstract_available` becomes true once any record has text. The complete file is replaced
  (the previous one kept as `.prev`); an import also removes a parked `.partial.json`.
- `data/conferences/config.yaml` holds `conferences: [{id, recipe, editions: {<year>: {from, until, volume?}}}]`; the optional
  `volume` keeps only that journal volume (ESMO AI is one volume per year);
  `id` becomes a directory name (lowercase letters, digits, `-`). No secrets are read or stored.

## Config example

```yaml
conferences:
  - id: aacr
    recipe: aacr
    editions:
      2026: {from: 2026-04-01, until: 2026-05-31}
  - id: esmo
    recipe: esmo
    editions:
      2025: {from: 2025-09-01, until: 2025-09-30}
  - id: esmo-ai
    recipe: esmo_ai
    editions:
      2025: {from: 2025-11-01, until: 2025-12-31, volume: 10}
      2026: {from: 2026-11-01, until: 2026-11-30, volume: 14}
```

## Extending

A new conference of a known type is a block in the config. A new type is a recipe in
`catalogue.py` (a new source kind also a downloader beside `crossref_supplement.py`); OpenReview is not built yet.

File, JSON-write and timestamp helpers (`atomic_write`, `write_json_atomic`, `now_iso_z`, `connect_sqlite_ro`, …) come from `services/_io`, not `_common`.
