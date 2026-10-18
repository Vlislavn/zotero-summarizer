# Export accepted digest evidence locally

After an explicitly requested deep review completes, export its existing cached
verification receipt. This does not generate a review. With the app already
running locally, replace `ABCDEFGH` with the exact eight-character Zotero key,
or a complete stable `feed:<namespace>:<64 lowercase hex characters>` key:

```sh
curl --fail --silent --show-error \
  'http://127.0.0.1:8000/api/library/deep-review/ABCDEFGH/audit' \
  --output digest-verification.json
```

**Sensitive local output:** the JSON contains digest text and original source
excerpts. Keep it private; do not upload it or commit it. The endpoint permits
only loopback peers/hosts and same-origin browser requests, sends `no-store`, and
uses the selected project's Settings-owned cache. It is not an authenticated
remote API; do not expose it through a proxy. No credentials, provider client,
source acquisition/extraction, cache repair/quarantine, model call, native Zotero
write, label or database write occurs in this export. Export never opens native
SQLite or invokes any native reader: even `mode=ro` can write WAL shared-memory
sidecars. The current base has no accessor proving a no-native-filesystem-write
boundary. A library-source receipt therefore cannot establish the current item
attachment. Its validated retained-source evidence remains inspectable, explicitly
separate from current-item evidence. Reader presence, absence or errors do not
change that conclusion. Explicit override sources retain their existing
source-specific override-wins identity contract without a native lookup.
Ordinary current-cache checks still use their existing attachment-identity owner;
unknown export currentness does not force unchanged native reviews to regenerate.
Normal review generation retains its existing effects, including optional notes
and brief rebuilding.

## Response contract

HTTP 200 returns `item_key`, `digest`, `audit_status`, `reason`,
`verification_audit`, and `retained_evidence`. `digest` is the exact stored
post-policy digest, not a new policy projection. `audit_status` is:

- `available`: the explicit override source still matches its receipt;
  `reason` is empty and `verification_audit` contains accepted evidence.
  This is not a claim about the current native attachment.
- `unavailable`: top-level receipt is null. `current_item_binding_unavailable`
  means that export cannot establish the current native item attachment, even
  if the old source file is unchanged. Other reasons include
  `legacy_audit_unavailable`, `source_unavailable` and
  `source_identity_unavailable`. Old reviews never acquire invented evidence.
- `stale`: top-level receipt is null. Changed cached digest/identity/receipt,
  extraction/verifier contracts, source bytes or review configuration invalidate
  retained-source qualification. Export never changes or deletes historical
  cache bytes; an explicitly generated review still replaces the existing entry.

When retained validation succeeds, `retained_evidence` contains
`scope="retained_source_only"`, `current_item_binding="not_established"`, and
`verification_audit` with the complete validated receipt. This allows inspection
of a library review's original evidence without certifying its current attachment.
An override may expose the same separately labelled historical view. Stale,
legacy, missing-source or failed validation never produces a successful retained
payload: `retained_evidence` is null (or malformed evidence is an HTTP error).

Missing review: 404 `not_found`. Invalid exact key: 422 `validation_error`.
Malformed cache/receipt/schema: 422 `audit_invalid`, never an empty successful
export or a scientific rejection. Disallowed local/origin boundary: 403
`local_only`; a stored source outside Settings' PDF roots returns 403
`path_not_allowed`. Filesystem/read failures remain operational errors.

The receipt contains:

- Exact accepted `verified_digest`, its hash, cached digest hash and explicit
  `post_verification_changes` (for example map-reduce basis and reading policy).
  Each retained claim identifies its actual verifier index, field, nested path,
  sentence-part number, text, numeric-preflight outcome and cited passage IDs.
  `matches_cached_field` prevents presenting changed findings as current support.
- Actual original and selected extraction text hashes/character extents,
  extraction/verifier contract hashes, source-byte and review-generation hashes.
  `complete_extracted_text` means only that every character of this extraction
  was supplied to verification; it does **not** establish a complete paper.
  `selected_original` identifies partial rank/prefix coverage.
- Verbatim original-evidence passages, selected coordinates and exact
  original-coordinate fragments. Coordinates are zero-based, end-exclusive
  Python Unicode-character offsets in the supplied extraction (after the
  review's existing outer-whitespace stripping), not PDF-byte offsets. Ranking preserves repeated occurrences by chunk index. Synthetic
  joining separators have no original coordinates. `pdf_page` stays null with
  `text_extraction_has_no_page_mapping`; page locations are never guessed.
- Initial/corrected digest hashes, generation parse retry, accepted verifier
  repair count, primary/fallback route, operational unavailability, and the
  existing correction outcome. No extra verifier call or ranking rule is added.

Generated map notes are generation context only. Both original-source
verification and its one correction/fallback retain original mappings. Literal
membership/offsets are mechanical checks, not entailment or scientific validity.
`supported=false` means support was not established, not adjudicated
contradiction. Unavailable/malformed verifier/source remains operational failure;
no evidence result writes training labels or changes Add eligibility policy.
`contradiction_adjudication` is `not_performed`. Configured model identity cannot
pin remotely mutable weights. Changing or removing an attachment invalidates
ordinary current-cache reuse even while the old file survives. Export does not
perform that native lookup and always leaves library current-item binding
unavailable; its separately labelled retained view is not a substitute. Override
sources follow their explicit original owner, not a subsequently attached PDF.
Checks are not an atomic filesystem/database snapshot or hostile-local-process
sandbox, and receipt hashes are consistency bindings, not signed attestations.

## Verification and limits

Parent-run regression selectors:

```sh
pytest -q tests/test_audit_read_boundary.py tests/test_verification_audit_export.py \
  tests/test_digest_verification.py \
  tests/test_review_source_boundary.py tests/test_review_cache.py \
  tests/test_review_identity.py tests/test_review_text.py tests/test_map_reduce.py \
  tests/test_deep_review.py tests/test_deep_review_pdf_override.py
```

Tests use scripted clients, temporary source/cache files and forbidden-effect
spies through production verifier/correction/map/worker/cache and the real HTTP
route. Scripted semantic responses test plumbing and failure distinctions, not
model accuracy. Source-only implementation checks cannot establish runtime
success. Neither a private/live-model audit nor the five-digest/ten-paper expert
effort pilot has been performed here; no time savings, human-study adoption or
issue closure is claimed. The separate optional interpretation experiment is
not part of this feature.
