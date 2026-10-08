# Browser article memory boundary

```text
OLD: Page.printToPDF -> Chromium prints/layouts the page -> OnPDFCreated(full PDF data)
                                                            -> optional CDP stream
     (the PDF exists before the stream can be drained or capped)

NEW: HTML-only page -> bounded decoded main-document stream -> fulfill HTML/JS
                    -> isolated CDP world -> complete admitted main-document body text
                    -> fixed local text layout -> bounded PDF sink -> atomic cache
```

## CAPA: remove the late print boundary

Chromium's `PageHandler::PrintToPDF` wires print completion to `OnPDFCreated`;
that callback receives `scoped_refptr<base::RefCountedMemory>` and only then calls
`CreateIOStreamFromData` for `ReturnAsStream`. A bounded stream reader therefore
limits transfer after Chromium has produced the full printed PDF, not the prior
print/layout allocation. The article paths now eliminate `Page.printToPDF` in
both `render_article_pdf` and the browser-fetch `render_fallback` path.

## Current contract

- The main-frame response is intercepted at CDP's Fetch response stage and read
  from a decoded stream before HTML is fulfilled; stale encoding/length headers
  are removed. Direct article rendering caps a non-PDF document at the admitted
  text budget; the persistent browser path caps its main-document response at the
  caller's `max_bytes`. These are main-document limits, not a cap on subresource
  traffic, whole-page browser activity, or browser RSS. Navigation waits for page
  load; scripts and subresources remain enabled.
- Text is collected from the selected main-frame document's `document.body` DOM
  text nodes in a CDP isolated world, in chunks no larger than 8 KiB. It skips
  text under `script`, `style`, `noscript`, and `template`; every node traversed
  within that body tree counts against the node budget. This is not a page
  facsimile: images, visual layout, CSS-generated content, embedded-frame content,
  and shadow-tree content are outside the collector scope. The guarantee is a
  complete walk of this admitted scope, not a claim to capture every visible or
  embedded page element.
- There is no prefix-clipping success path. If the body text exceeds its byte
  budget, traversal exhausts the node budget or caller-derived monotonic deadline,
  the document/URL or DOM changes, text is invalid/empty, or collection is
  incomplete, acquisition fails closed instead of returning a partial-text PDF.
  PyMuPDF lays admitted text on fixed 612 x 792 pt pages with built-in Helvetica
  for ASCII and its built-in `cjk` Unicode font for non-ASCII glyphs, subset by
  PyMuPDF itself (no external font source or `fontTools` dependency). Unsupported
  glyphs, control characters, page-limit failures, or a failed text round-trip
  also fail closed. The PDF Subject value is exactly: "Text-only conversion;
  original visual layout is not preserved."
- The PDF writer's byte sink checks each write before retaining it and rejects
  output above the caller's `max_bytes`. The cache is written atomically only
  after a complete valid PDF exists; extraction/render-budget failures publish
  no PDF and leave no cache temporary file.

| Budget | Default | Hard maximum | Setting |
|---|---:|---:|---|
| Extracted source text, UTF-8 bytes | 1,000,000 | 4,000,000 | `ZS_ARTICLE_PDF_MAX_TEXT_BYTES` |
| Output PDF pages | 256 | 512 | `ZS_ARTICLE_PDF_MAX_PAGES` |
| Visited DOM nodes | 100,000 | 1,000,000 | `ZS_BROWSER_ARTICLE_NODE_BUDGET` |

The source-text limit is also capped by the caller's `max_bytes`; the output sink
uses that PDF-size limit. The main-document response cap does not cap subresources
or all browser network activity. DOM traversal has both a configurable node-work
cap and the caller-derived monotonic timeout; it does not silently stop early and
save a prefix. These are application-level work/output bounds, **not** a browser
sandbox or a whole-process/OS RSS limit. Page parsing, layout, live DOM/JavaScript,
image/font decoding, and native-library transient allocations are not bounded by
the captured-byte caps and may exceed or coexist with them. `max_bytes` therefore
provides no same-sized peak-RAM guarantee and there is no OS memory-isolation claim.

## Cache, preserved behavior, and proof status

Both text-snapshot paths use the same physical namespace:

```text
cache_dir/
  <first 16 hex of SHA-256(raw URL)>.pdf            # captured source PDF; unchanged
  article-snapshots/
    <full SHA-256(raw URL)>.pdf                    # text snapshot
```

The direct `render_article_pdf` path and persistent `render_fallback` path use the
full SHA-256 of the exact raw URL passed to that acquisition call under
`article-snapshots/`; the cache hash is separate from acquisition provenance. The service classifies a result as `web_article=True` only when its path equals
`article_snapshot_path(source_url, cache_dir)`. Genuine captured/downloaded PDFs keep the raw-URL cache, `web_article=False`, and browser/source provenance.
A persistent snapshot is reused only when `render_fallback=True`, after checking the
existing publisher-URL PDF cache first.
Previously written prefixed root-level article-cache files remain untouched: there
is no compatibility alias, migration, deletion, or reclassification. Existing `source_url`, publisher-PDF cache key, and direct pure-web-article metadata remain unchanged.
Direct and authenticated PDF acquisition are unchanged; only the HTML-text derivative
uses the new snapshot format. A declared scholarly PDF that cannot be fetched does not
become a rendered paywall/login stub. No UI choice is added.

Deterministic contracts exist for both article paths, complete admitted-scope text
retention, UTF-8/node/output limits, fail-closed mutation, and temp cleanup in
[`test_article_budget_acceptance.py`](../tests/test_article_budget_acceptance.py)
and [`test_browser_article_collector.py`](../tests/test_browser_article_collector.py).
Final live proof is recorded in `data/a111-live-final-provenance/summary.md`:
**18 passed, 0 failed, 0 skipped**, including installed-Chrome controls and bundled
Chromium; the 217-call CDP capture contains **zero `Page.printToPDF`** calls. The
live full-Unicode fixture's PDF text round-trip passed, and the parent visually
checked the rendered Greek/Russian glyphs as clean. A 4,096-byte genuine PDF was
retained byte-for-byte in the raw-URL cache despite an 8-byte text budget; no text
writer or snapshot cache was used, and the provenance test kept
`web_article=False`/`source="browser"`. Text snapshots use only the canonical
`article_snapshot_path` folder/hash; the legacy root-level cache is left untouched.

Release gates are in `data/a111-release-gates/summary.md`: 9/9 hooks, 231 focused
passed/30 skipped, 4,246 forked passed/39 skipped/0 failed, 4,244 serial
passed/39 skipped/2 unchanged baseline failures, and CLI route count 117. Guard
samples in the live receipt were 726,499,328 bytes peak tree RSS, 46% minimum free
RAM, and zero swap growth. These are observed run measurements, not an RSS cap or
OS/browser memory-safety proof. The output remains a bounded text projection, not
the source page's visual layout. A124/A136 semantic accuracy was not measured.

## Primary implementation and API sources

- [Decoded Fetch response limits](../zotero_summarizer/integrations/_browser_response.py#L44-L89),
  [response capture](../zotero_summarizer/integrations/_browser_response.py#L102-L157),
  [shared snapshot and publisher cache paths](../zotero_summarizer/integrations/browser_fetch.py#L151-L158),
  [direct and persistent cache behavior](../zotero_summarizer/integrations/browser_fetch.py#L215-L330),
  [persistent fallback](../zotero_summarizer/integrations/browser_fetch.py#L361-L411),
  [node budget](../zotero_summarizer/integrations/_browser_article.py#L8-L14),
  [body text collector](../zotero_summarizer/integrations/_browser_article.py#L16-L99),
  [collector completion checks](../zotero_summarizer/integrations/_browser_article.py#L232-L285),
  [text budget and PDF Subject](../zotero_summarizer/integrations/_article_pdf.py#L14-L63),
  [page budget](../zotero_summarizer/integrations/_article_pdf.py#L177-L214),
  and [bounded output sink and text round-trip](../zotero_summarizer/integrations/_article_pdf.py#L134-L214).
  The [caller routing](../zotero_summarizer/services/library/_pdf_acquire.py#L81-L129)
  keeps web-article and scholarly-PDF paths distinct.
- [Chromium `PageHandler::PrintToPDF` / `OnPDFCreated`](https://github.com/chromium/chromium/blob/main/chrome/browser/devtools/protocol/page_handler.cc#L88-L135): completion callback, full ref-counted PDF data, then stream creation.
- PyMuPDF's existing locked version is **1.27.2.3** ([`uv.lock`](../uv.lock#L5484)); the writer uses [`Document.save`](https://pymupdf.readthedocs.io/en/latest/document.html#Document.save) and [`Page.insert_text`](https://pymupdf.readthedocs.io/en/latest/page.html#Page.insert_text). Package metadata identifies the dual license as **GNU AGPL-3.0 or Artifex Commercial License** ([license](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)); no dependency is added here.
