# pdf-inspector benchmark runbook

## Where changes go

- Parser adapter: `zotero_summarizer/integrations/pdf_inspector.py`
- Routing/fallback: `zotero_summarizer/services/library/_paper_read_pdf.py`
- Optional dependency: `pyproject.toml`
- Benchmark only: `tools/bench_pdf_parsers.py`
- Decision criteria: `docs/specs/pdf-inspector-evaluation.md`

Do not move figure cropping or hyperlink extraction out of PyMuPDF.

## Run

Create a local, untracked JSONL manifest:

```json
{"id":"paper-001","stratum":"two-column","path":"/absolute/path/paper.pdf"}
```

Then:

```bash
uv run python tools/bench_pdf_parsers.py --self-check
uv pip install pdf-inspector
uv run python tools/bench_pdf_parsers.py data/pdf-benchmark-manifest.jsonl \
  --output data/pdf-parser-benchmark.json
```

The output records document hash, parser, success/failure, latency, extracted character count, replacement characters, page count, PDF type, and OCR-page signals when exposed by the installed binding.

Use at least 20 representative papers for the first go/no-go check. Manually inspect paired outputs for reading order, missing sections, tables, and duplicated paragraphs. Expand to the 60-document frozen corpus only if the package installs and the first sample shows a plausible benefit.

## Minimal decision

Continue integration only when:

- installation works without requiring users to build Rust;
- failures fall back instead of aborting;
- native-text reading order/tables are visibly better than the current path;
- downstream evidence retrieval is not worse;
- the latency benefit over Docling is material.

Otherwise remove the experiment. Full thresholds are in the specification.

## Recorded smoke result — 2026-08-05

Executed locally against one generated native-text PDF:

```text
Python: 3.13.5
PyMuPDF: 1.26.7
pdf-inspector: not installed in the execution environment
PyMuPDF documents: 1
successes: 1
failures: 0
median latency: 58.574 ms
extracted characters: 71
replacement characters: 0
```

Checks passed:

```text
python -m py_compile tools/bench_pdf_parsers.py
python tools/bench_pdf_parsers.py --self-check
python tools/bench_pdf_parsers.py <generated-manifest> --output <result.json>
```

This validates the harness and PyMuPDF baseline path only. It is not evidence for adopting `pdf-inspector`; that requires an environment where the package can be installed plus the real Zotero corpus.
