#!/usr/bin/env python3
"""Minimal PDF parser benchmark for the pdf-inspector adoption decision."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable


def _version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pymupdf(path: Path) -> dict[str, Any]:
    import fitz

    with fitz.open(path) as doc:
        text = "\n\n".join(page.get_text("text") for page in doc)
        return {"text": text, "pages": doc.page_count, "kind": "text"}


def _pdf_inspector(path: Path) -> dict[str, Any]:
    import pdf_inspector

    result = pdf_inspector.process_pdf(str(path))
    return {
        "text": result.markdown or "",
        "pages": getattr(result, "page_count", None),
        "kind": str(result.pdf_type),
        "pages_needing_ocr": list(getattr(result, "pages_needing_ocr", []) or []),
    }


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        row = json.loads(line)
        pdf = Path(row["path"]).expanduser()
        if not pdf.is_absolute():
            pdf = (path.parent / pdf).resolve()
        if not pdf.is_file():
            raise FileNotFoundError(f"manifest line {line_no}: {pdf}")
        rows.append({**row, "path": str(pdf)})
    if not rows:
        raise ValueError("manifest contains no PDFs")
    return rows


def _run_one(parser: str, fn: Callable[[Path], dict[str, Any]], row: dict[str, Any]) -> dict[str, Any]:
    path = Path(row["path"])
    started = time.perf_counter()
    try:
        parsed = fn(path)
        elapsed_ms = (time.perf_counter() - started) * 1000
        text = parsed.pop("text")
        return {
            "id": row.get("id", path.stem),
            "stratum": row.get("stratum", "unclassified"),
            "sha256": _sha256(path),
            "parser": parser,
            "ok": True,
            "latency_ms": round(elapsed_ms, 3),
            "chars": len(text),
            "replacement_chars": text.count("\ufffd"),
            **parsed,
        }
    except Exception as exc:
        return {
            "id": row.get("id", path.stem),
            "stratum": row.get("stratum", "unclassified"),
            "sha256": _sha256(path),
            "parser": parser,
            "ok": False,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_parser: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        by_parser.setdefault(row["parser"], []).append(row)
    output: dict[str, Any] = {}
    for parser, rows in sorted(by_parser.items()):
        good = [row for row in rows if row["ok"]]
        output[parser] = {
            "documents": len(rows),
            "successes": len(good),
            "failures": len(rows) - len(good),
            "median_latency_ms": round(statistics.median(row["latency_ms"] for row in good), 3) if good else None,
            "median_chars": round(statistics.median(row["chars"] for row in good)) if good else None,
        }
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path, nargs="?")
    parser.add_argument("--output", type=Path, default=Path("data/pdf-parser-benchmark.json"))
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()

    environment = {
        "python": sys.version.split()[0],
        "pymupdf": _version("PyMuPDF"),
        "pdf_inspector": _version("pdf-inspector"),
    }
    if args.self_check:
        print(json.dumps(environment, indent=2, sort_keys=True))
        return 0 if environment["pymupdf"] else 1
    if args.manifest is None:
        parser.error("manifest is required unless --self-check is used")

    engines: list[tuple[str, Callable[[Path], dict[str, Any]]]] = [("pymupdf", _pymupdf)]
    if environment["pdf_inspector"]:
        engines.append(("pdf_inspector", _pdf_inspector))

    results = [
        _run_one(name, fn, row)
        for row in _load_manifest(args.manifest)
        for name, fn in engines
    ]
    payload = {"environment": environment, "summary": _summary(results), "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
