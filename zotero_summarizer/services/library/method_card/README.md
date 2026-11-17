# `services/library/method_card/` — "How it works" card

One card per paper (REQ doc: `method-card-requirements-design.md`, rev.3, stage 2a): **core idea**, **what it
was compared against**, **what the authors claim is new**, **how to reproduce** — every row tied to a
sentence that exists in the paper. No novelty check and no auto-trigger yet (stages 3 / 2b). Not the Today
card (`services/card`): that one has its own stage, consent and budget; only its storage pattern is borrowed.

```
POST /api/library/method-card/{key} ─> _job.request(start=True) ─ one job per key, own 1-thread pool (never the review queue)
   _build: review identity (source path + sha) ─ extract_pdf_content (sections) + _paper_body.read_body (whole body, no references)
           ─ body > provider cap ⇒ `too_long` (never truncated)
           ─ ONE model call, stage `deep_review`, thinking off  (_prompt.build_prompt: paper_block first)
           ─ _verify.verify_card ─> sidecar (_store)
GET  /api/library/method-card/{key} ─> same state, never starts a call (the page polls it every 3 s while `building`)
```

| file | responsibility |
|---|---|
| `_prompt.py` | prompt, `CardResponse` schema, `prompt_sha()` (stamp part). **Outside** `_review_identity._prompt_contract_text`: editing it re-stamps cards, never reviews |
| `_verify.py` | strict grounding of every quote (`_grounding.quote_is_grounded(fuzzy=False)`) + `answer_is_supported_by_quote`; compared rows need their numbers inside the quote; section via `_review_section_overlay._locate_quote_kind` (reference sections excluded); an ungrounded core idea raises `CardNotGrounded` ⇒ `failed`, never a partial card; `short_text` is assembled from verified parts |
| `_store.py` | sidecar `paper_render_dir/_method_card/<key>-<source sha16>.json`, atomic write, older cards of the same key removed; valid only for its stamp `source_sha:prompt_sha:model` |
| `_job.py` | `request(key, start=)`, job registry, failure memory |

**States** (`state` + `reason` + `can_build` + `retry_in_seconds` + `card`): `ready` · `building` ·
`failed` (`not_grounded`, `model_error`, `source_changed`, `no_text`; a retry is refused for 300 s) ·
`unavailable` (`not_built` + `can_build` = the Build button; `no_review`, `lean_provider`, `too_long`,
`review_running` = a local provider waits for a running review).

**Contract:** the card never writes `deep_reviews.json`, never changes `review_revision`, the review
identity or the Zotero note, and a re-review does not erase it (the stamp is compared afresh). Stored fields:
`core_idea{text,quote,section}`, `compared[{what,result,quote,section}]` (paper only), `novelty{claim_quote,
section,check_state:"not_run",checks:[]}`, `reuse[{input,operation,output,quote,section}]` (≤3), `use_when`,
`caveat{text,quote,section}`, `short_text`, `timing{seconds,prompt_tokens,completion_tokens,chars}`, `model`,
`generated_at`. `section` is `§ Title`, `p.N` or `""` (quote not located in a section).

Tests: `tests/test_method_card.py` (every shown quote is in the paper; a build leaves `deep_reviews.json` and
`review_revision` untouched; one job per key; 300 s retry window).
