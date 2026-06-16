"""PDF fallback primitives for the paper-read pipeline."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import fitz

_ARXIV_RE = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5})(v\d+)?", re.IGNORECASE)
_CAPTION_RE = re.compile(r"^\s*(Figure|Fig\.?|Table)\s+(\d+)[.:]?\s+(.+)", re.IGNORECASE)
_SECTION_RE = re.compile(
    r"^\s*(Abstract|Introduction|Background|Related Work|Methods?|Methodology|"
    r"Approach|Experiments?|Results?|Discussion|Limitations?|Conclusion|"
    r"References|Acknowledg(?:e)?ments|Appendix)\s*$",
    re.IGNORECASE,
)
_NUMBERED_SECTION_RE = re.compile(r"^\s*(\d+(?:\.\d+)*\.?)\s+([A-Z][^\n]{2,90})$")
_REF_LINE_RE = re.compile(r"^\s*(?:\[\d+\]|\d+\.)\s+\S+")


def detect_arxiv_id(pdf_path: Path) -> str | None:
    """Return an arXiv id found on the first/last page, if present."""
    with fitz.open(pdf_path) as doc:
        page_numbers = [0]
        if doc.page_count > 1:
            page_numbers.append(doc.page_count - 1)
        for page_no in page_numbers:
            text = doc[page_no].get_text("text")
            match = _ARXIV_RE.search(text)
            if match:
                return f"{match.group(1)}{match.group(2) or ''}"
    return None


def _block_texts(page: fitz.Page) -> list[tuple[float, str]]:
    blocks: list[tuple[float, str]] = []
    for block in page.get_text("blocks"):
        text = str(block[4] or "").strip()
        if text:
            blocks.append((float(block[1]), text))
    return sorted(blocks, key=lambda item: item[0])


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if _SECTION_RE.match(stripped):
        return True
    numbered = _NUMBERED_SECTION_RE.match(stripped)
    if numbered and len(stripped.split()) <= 12:
        return True
    return False


def extract_pdf_content(pdf_path: Path) -> dict[str, Any]:
    """Extract metadata, section-ish text, and reference count from a PDF."""
    with fitz.open(pdf_path) as doc:
        meta = doc.metadata or {}
        pages: list[str] = []
        sections: list[dict[str, Any]] = []
        current_title = "Front matter"
        current_page = 1
        parts: list[str] = []

        def flush() -> None:
            text = "\n\n".join(parts).strip()
            if text:
                sections.append(
                    {
                        "id": f"sec-{len(sections) + 1}",
                        "title": current_title,
                        "level": 1,
                        "page": current_page,
                        "text": text,
                    }
                )

        for page_no, page in enumerate(doc):
            page_text_lines: list[str] = []
            for _y, block_text in _block_texts(page):
                lines = [line.strip() for line in block_text.splitlines() if line.strip()]
                page_text_lines.extend(lines)
                if len(lines) == 1 and _looks_like_heading(lines[0]):
                    flush()
                    current_title = lines[0]
                    current_page = page_no + 1
                    parts = []
                else:
                    parts.append(block_text)
            pages.append("\n".join(page_text_lines))
        flush()

        full_text = "\n\n".join(pages).strip()
        title = str(meta.get("title") or "").strip() or _title_from_text(full_text) or pdf_path.stem
        refs = _count_references(full_text)
        return {
            "title": title,
            "authors": str(meta.get("author") or "").strip(),
            "keywords": _split_keywords(str(meta.get("keywords") or "")),
            "n_pages": doc.page_count,
            "sections": sections or _sections_by_page(pages),
            "full_text": full_text,
            "references_count": refs,
        }


def extract_pdf_figures(pdf_path: Path, figures_dir: Path, *, max_figures: int = 30) -> list[dict[str, Any]]:
    """Render figure/table regions from PDF page clips.

    Crops the *actual* graphic region (vector drawings + raster images detected
    near the caption) rather than a blind fixed band, so a figure's plot/labels
    survive without grabbing adjacent columns. Captions are deduped by label so
    an in-prose "Figure 1 shows…" doesn't inflate the figure count.
    """
    figures_dir.mkdir(parents=True, exist_ok=True)
    figures: list[dict[str, Any]] = []
    with fitz.open(pdf_path) as doc:
        for cand in _figure_candidates(doc):
            if len(figures) >= max_figures:
                break
            clip = cand["clip"]
            if clip.is_empty or clip.get_area() < 1000:
                continue
            pix = doc[cand["page_no"]].get_pixmap(dpi=200, clip=clip, alpha=False)
            name = _figure_name(len(figures) + 1, cand["caption"])
            pix.save(str(figures_dir / name))
            figures.append(
                {
                    "name": name,
                    "page": cand["page_no"] + 1,
                    "caption": cand["caption"],
                    "label": cand["label"],
                    "bbox": [round(clip.x0, 1), round(clip.y0, 1), round(clip.x1, 1), round(clip.y1, 1)],
                    "source": "pdf-region",
                }
            )
    return figures


def _figure_candidates(doc: fitz.Document) -> list[dict[str, Any]]:
    """Caption candidates deduped by label, in document order.

    The caption regex also matches in-prose mentions; dedup keeps the candidate
    backed by an actual graphic (largest area), else the longest caption text."""
    best_by_label: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for page_no, page in enumerate(doc):
        for caption in _caption_blocks(page):
            clip, graphic_area = _figure_clip(page, caption)
            label = caption["label"].lower()
            cand = {
                "label": caption["label"],
                "caption": caption["caption"],
                "page_no": page_no,
                "clip": clip,
                "graphic_area": graphic_area,
                "caption_len": len(caption["caption"]),
            }
            prev = best_by_label.get(label)
            if prev is None:
                best_by_label[label] = cand
                order.append(label)
            elif _better_candidate(cand, prev):
                best_by_label[label] = cand
    return [best_by_label[label] for label in order]


def _better_candidate(cand: dict[str, Any], prev: dict[str, Any]) -> bool:
    if cand["graphic_area"] != prev["graphic_area"]:
        return cand["graphic_area"] > prev["graphic_area"]
    return cand["caption_len"] > prev["caption_len"]


def _figure_clip(page: fitz.Page, caption: dict[str, Any]) -> tuple[fitz.Rect, float]:
    """Best crop for a caption: union of the nearest graphic cluster + caption.

    Figures caption *below* their graphic (look up), tables caption *above*
    their body (look down). Returns ``(clip, graphic_area)`` — area 0 when no
    graphic was detected and a tightened fallback band is used."""
    cap = caption["bbox"]
    is_table = caption["label"].lower().startswith("table")
    best: fitz.Rect | None = None
    best_dist: float | None = None
    for cluster in _merge_rects(_graphic_rects(page)):
        if is_table:
            if cluster.y1 <= cap.y0:  # graphic entirely above caption → not this table's body
                continue
            dist = abs(cluster.y0 - cap.y1)
        else:
            if cluster.y0 >= cap.y1:  # graphic entirely below caption → not this figure
                continue
            dist = abs(cap.y0 - cluster.y1)
        if best_dist is None or dist < best_dist:
            best, best_dist = cluster, dist
    if best is not None and best_dist is not None and best_dist < 260:
        return ((best | cap) & page.rect), best.get_area()
    # Fallback (no detectable graphic): a tighter band than the old ±320/70 blind crop.
    x0, x1 = page.rect.x0 + 24, page.rect.x1 - 24
    if is_table:
        band = fitz.Rect(x0, cap.y0 - 6, x1, cap.y1 + 240)
    else:
        band = fitz.Rect(x0, cap.y0 - 240, x1, cap.y1 + 8)
    return (band & page.rect), 0.0


def _graphic_rects(page: fitz.Page) -> list[fitz.Rect]:
    """Image + vector-drawing bounding boxes that plausibly belong to a figure
    (drops hairline rules, tiny marks and full-page backgrounds)."""
    page_area = abs(page.rect.get_area()) or 1.0
    rects: list[fitz.Rect] = []
    for info in page.get_image_info():
        rect = fitz.Rect(info["bbox"])
        if not rect.is_empty and rect.get_area() > 0.01 * page_area:
            rects.append(rect)
    for draw in page.get_drawings():
        rect = fitz.Rect(draw["rect"])
        area = rect.get_area()
        if rect.is_empty or area < 0.01 * page_area or area > 0.9 * page_area:
            continue
        rects.append(rect)
    # Bound the O(n^2) merge cost on vector-heavy figures: keep the largest.
    rects.sort(key=lambda r: r.get_area(), reverse=True)
    return rects[:400]


def _merge_rects(rects: list[fitz.Rect], *, gap: float = 18.0) -> list[fitz.Rect]:
    """Union rects that overlap or sit within ``gap`` pts — a multi-panel figure
    becomes one bounding box."""
    boxes = [fitz.Rect(r) for r in rects]
    merged = True
    while merged:
        merged = False
        out: list[fitz.Rect] = []
        for rect in boxes:
            placed = False
            for idx, existing in enumerate(out):
                grown = fitz.Rect(existing.x0 - gap, existing.y0 - gap, existing.x1 + gap, existing.y1 + gap)
                if grown.intersects(rect):
                    out[idx] = existing | rect
                    placed = True
                    merged = True
                    break
            if not placed:
                out.append(fitz.Rect(rect))
        boxes = out
    return boxes


def _caption_blocks(page: fitz.Page) -> list[dict[str, Any]]:
    captions: list[dict[str, Any]] = []
    for block in page.get_text("blocks"):
        text = str(block[4] or "").strip().replace("\n", " ")
        match = _CAPTION_RE.match(text)
        if not match:
            continue
        label = f"{match.group(1)} {match.group(2)}"
        captions.append({"label": label, "caption": text, "bbox": fitz.Rect(block[:4])})
    return sorted(captions, key=lambda item: item["bbox"].y0)


def _figure_name(index: int, caption: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", caption.lower())[:6]
    suffix = "_".join(words) or "figure"
    return f"fig{index}_{suffix}.png"


def _title_from_text(text: str) -> str:
    for line in text.splitlines()[:20]:
        stripped = line.strip()
        if 8 <= len(stripped) <= 180 and not _ARXIV_RE.search(stripped):
            return stripped
    return ""


def _split_keywords(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;]", raw or "") if part.strip()][:8]


def _sections_by_page(pages: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "id": f"sec-{idx + 1}",
            "title": f"Page {idx + 1}",
            "level": 1,
            "page": idx + 1,
            "text": text,
        }
        for idx, text in enumerate(pages)
        if text.strip()
    ]


def _count_references(text: str) -> int:
    ref_text = text
    marker = re.search(r"\n\s*References\s*\n", text, flags=re.IGNORECASE)
    if marker:
        ref_text = text[marker.end():]
    return len([line for line in ref_text.splitlines() if _REF_LINE_RE.match(line)])
