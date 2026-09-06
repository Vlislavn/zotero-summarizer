"""TeX-source acquisition and parsing for the paper-read pipeline."""
from __future__ import annotations

import io
import re
import shutil
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urljoin

import fitz
import httpx

from zotero_summarizer.integrations.app_rss import stream_public_url
from zotero_summarizer.settings import offline_requested

# Bounds on response bytes, extracted file bytes and archive members.
# Failures propagate; only offline policy and HTTP 404 mean no source attempt/result.
_MAX_ARCHIVE_BYTES = 80 * 1024 * 1024  # compressed download cap
_MAX_EXTRACT_BYTES = 200 * 1024 * 1024  # total uncompressed cap
_MAX_MEMBERS = 5000

_TITLE_RE = re.compile(r"\\title(?:\[[^\]]*\])?\{(?P<body>.*?)\}", re.DOTALL)
_AUTHOR_RE = re.compile(r"\\author(?:\[[^\]]*\])?\{(?P<body>.*?)\}", re.DOTALL)
_ABSTRACT_RE = re.compile(r"\\begin\{abstract\}(?P<body>.*?)\\end\{abstract\}", re.DOTALL)
_KEYWORDS_RE = re.compile(r"\\(?:keywords|icmlkeywords|acmkeywords)\{(?P<body>.*?)\}", re.DOTALL)
_SECTION_RE = re.compile(r"\\(?P<kind>section|subsection|subsubsection)\*?\{(?P<title>.*?)\}", re.DOTALL)
_FIGURE_RE = re.compile(r"\\begin\{figure\*?\}(?P<body>.*?)\\end\{figure\*?\}", re.DOTALL)
_CAPTION_RE = re.compile(r"\\caption(?:\[[^\]]*\])?\{(?P<body>.*?)\}", re.DOTALL)
_LABEL_RE = re.compile(r"\\label\{(?P<label>[^}]+)\}")
_INCLUDE_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{(?P<path>[^}]+)\}")
_REF_RE = re.compile(r"\\(?:auto)?ref\{(?P<label>[^}]+)\}")
_BIBITEM_RE = re.compile(r"\\bibitem(?:\[[^\]]*\])?\{")
# Environments whose content is garbage when extracted as prose. The matching
# \end is pinned to the same env via a backreference so nested/adjacent envs of
# different kinds don't cross-match. (tabularx before tabular for clean matching.)
_STRIP_ENVS = (
    "equation", "align", "alignat", "flalign", "eqnarray", "gather", "multline",
    "split", "array", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix",
    "smallmatrix", "matrix", "cases", "displaymath", "math",
    "tabularx", "tabular", "table", "figure",
    "algorithm", "algorithmic", "algorithm2e", "lstlisting", "verbatim",
    "minted", "tikzpicture",
)
_STRIP_ENV_RE = re.compile(
    r"\\begin\{(?P<env>" + "|".join(_STRIP_ENVS) + r")\*?\}.*?\\end\{(?P=env)\*?\}",
    re.DOTALL,
)
_MATH_DISPLAY_RE = re.compile(r"\\\[.*?\\\]|\$\$.*?\$\$", re.DOTALL)
_MATH_PAREN_RE = re.compile(r"\\\(.*?\\\)", re.DOTALL)
# \cmd[opt]{arg} → arg (innermost only; applied until stable for nested braces).
_TEX_INNER_ARG_RE = re.compile(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}")
# Inline $…$: span newlines, allow long expressions (the old 200-char/no-newline
# bound let `$G = (N, E, S)$`-style math leak). `[^$]` can't cross a delimiter.
_MATH_INLINE_RE = re.compile(r"\$[^$]{1,1000}\$", re.DOTALL)


def download_arxiv_source(arxiv_id: str, source_dir: Path) -> Path | None:
    """Publish a complete TeX tarball; offline/404 mean absence, errors propagate.

    Existing nonempty sources belong to the user and are never merged/replaced.
    A same-filesystem rename publishes only after extraction and TeX validation.
    """
    if offline_requested():
        return None
    if source_dir.is_symlink():
        raise ValueError("Source directory cannot be a symlink")
    if source_dir.exists() and any(source_dir.iterdir()):
        raise FileExistsError(f"Source directory is not empty: {source_dir}")
    data = _download_capped(f"https://arxiv.org/e-print/{arxiv_id}")
    if data is None:
        return None
    source_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=source_dir.parent, prefix=".arxiv-source-") as staging:
        staged = Path(staging) / "source"
        staged.mkdir()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*", errorlevel=2) as tar:
            _safe_extract(tar, staged)
        if not any(path.is_file() and path.stat().st_size for path in staged.rglob("*.tex")):
            raise tarfile.TarError("Source archive contains no nonempty TeX file")
        staged.replace(source_dir)
    return source_dir


def _download_capped(url: str) -> bytes | None:
    """Bound raw response bytes; every redirect re-enters the public-IP boundary."""
    with httpx.Client(timeout=60.0, trust_env=False, headers={"Accept-Encoding": "identity"},
                      limits=httpx.Limits(max_keepalive_connections=0)) as client:
        for _ in range(6):
            if httpx.URL(url).scheme != "https":
                raise ValueError("arXiv source requires HTTPS")
            with stream_public_url(client, url) as response:
                if 300 <= response.status_code < 400 and response.headers.get("location"):
                    url = urljoin(url, response.headers["location"])
                    continue
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ValueError("Unsupported arXiv source HTTP encoding")
                body = bytearray()
                for chunk in response.iter_raw(chunk_size=64 * 1024):
                    if len(body) + len(chunk) > _MAX_ARCHIVE_BYTES:
                        raise ValueError("arXiv source exceeds response size limit")
                    body.extend(chunk)
                return bytes(body)
    raise ValueError("arXiv source redirected too many times")


def parse_tex_source(source_dir: Path, figures_dir: Path) -> dict[str, Any]:
    """Parse TeX content and copy/convert referenced figures."""
    tex_files = _ordered_tex_files(source_dir)
    combined = "\n\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in tex_files)
    sections = _sections_from_tex(combined)
    figures = _figures_from_tex(combined, source_dir, figures_dir)
    return {
        "title": _clean_tex(_first_group(_TITLE_RE, combined)) or source_dir.parent.stem,
        "authors": _clean_tex(_first_group(_AUTHOR_RE, combined)),
        "abstract": _clean_tex(_first_group(_ABSTRACT_RE, combined)),
        "keywords": [_clean_tex(part) for part in re.split(r"[,;]", _first_group(_KEYWORDS_RE, combined)) if part.strip()],
        "sections": sections,
        "figures": figures,
        "full_text": _clean_tex(combined),
        "references_count": _count_references(source_dir, combined),
        "tex_files": [str(path) for path in tex_files],
    }


def _ordered_tex_files(source_dir: Path) -> list[Path]:
    files = sorted(source_dir.rglob("*.tex"))
    main = [
        path for path in files
        if re.search(r"\\documentclass|\\begin\{document\}", path.read_text(encoding="utf-8", errors="ignore"))
    ]
    ordered = main + [path for path in files if path not in main]
    return ordered[:60]


def _sections_from_tex(text: str) -> list[dict[str, Any]]:
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        abstract = _clean_tex(_first_group(_ABSTRACT_RE, text))
        return [{"id": "sec-1", "title": "Abstract", "level": 1, "page": 1, "text": abstract}] if abstract else []
    sections: list[dict[str, Any]] = []
    levels = {"section": 1, "subsection": 2, "subsubsection": 3}
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        sections.append(
            {
                "id": f"sec-{idx + 1}",
                "title": _clean_tex(match.group("title")),
                "level": levels.get(match.group("kind"), 1),
                "page": 1,
                "text": _clean_tex(text[start:end]),
            }
        )
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for sec in sections:
        key = sec["title"].strip().lower()
        if not sec.get("text", "").strip() or key in seen:
            continue
        seen.add(key)
        result.append(sec)
    return result


def _figures_from_tex(text: str, source_dir: Path, figures_dir: Path) -> list[dict[str, Any]]:
    figures_dir.mkdir(parents=True, exist_ok=True)
    figures: list[dict[str, Any]] = []
    for match in _FIGURE_RE.finditer(text):
        body = match.group("body")
        include = _INCLUDE_RE.search(body)
        caption = _clean_tex(_first_group(_CAPTION_RE, body))
        label_match = _LABEL_RE.search(body)
        label = label_match.group("label") if label_match else f"fig:{len(figures) + 1}"
        if not include:
            figures.append(_placeholder_figure(len(figures) + 1, caption, label))
            continue
        src = _resolve_figure_path(source_dir, include.group("path"))
        if src is None:
            figures.append(_placeholder_figure(len(figures) + 1, caption, label))
            continue
        out_name = _figure_name(len(figures) + 1, label, src)
        out_path = figures_dir / out_name
        if src.suffix.lower() == ".pdf":
            with fitz.open(src) as doc:
                pix = doc[0].get_pixmap(matrix=fitz.Matrix(3.0, 3.0), alpha=False)
                pix.save(str(out_path.with_suffix(".png")))
                out_name = out_path.with_suffix(".png").name
        elif src.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            shutil.copyfile(src, out_path)
        else:
            figures.append(_placeholder_figure(len(figures) + 1, caption, label))
            continue
        figures.append(
            {
                "name": out_name,
                "page": 1,
                "caption": caption,
                "label": label,
                "source": "tex",
                "original_path": str(src),
                "refs": _REF_RE.findall(body),  # scoped to figure env, not whole doc
            }
        )
    return figures


def _resolve_figure_path(source_dir: Path, raw: str) -> Path | None:
    root = source_dir.resolve()
    candidate = (root / raw).expanduser()
    options = [candidate]
    if not candidate.suffix:
        options.extend(candidate.with_suffix(ext) for ext in (".pdf", ".png", ".jpg", ".jpeg", ".eps"))
    for path in options:
        if path.is_file():
            resolved = path.resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            return resolved
    # Case-insensitive fallback (Linux fs is case-sensitive; arXiv sources vary)
    stem_lower = Path(raw).stem.lower()
    suffix_lower = Path(raw).suffix.lower()
    suffixes = {suffix_lower} if suffix_lower else {".pdf", ".png", ".jpg", ".jpeg", ".eps"}
    for p in source_dir.rglob("*"):
        if p.is_file() and p.stem.lower() == stem_lower and p.suffix.lower() in suffixes:
            return p.resolve()
    return None


def _figure_name(index: int, label: str, src: Path) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", label.replace("fig:", "")).strip("_").lower()
    return f"fig{index}_{safe or src.stem}{src.suffix.lower()}"


def _placeholder_figure(index: int, caption: str, label: str) -> dict[str, Any]:
    return {
        "name": "",
        "page": 1,
        "caption": caption,
        "label": label,
        "source": "tex-placeholder",
        "missing": True,
    }


def _count_references(source_dir: Path, text: str) -> int:
    bib_count = sum(1 for _ in source_dir.rglob("*.bib"))
    if bib_count:
        return sum(
            len(re.findall(r"^\s*@", path.read_text(encoding="utf-8", errors="ignore"), flags=re.MULTILINE))
            for path in source_dir.rglob("*.bib")
        )
    return len(_BIBITEM_RE.findall(text))


def _first_group(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group("body") if match else ""


def _sub_until_stable(pattern: re.Pattern[str], repl: str, text: str, *, passes: int = 3) -> str:
    """Apply a substitution until it stops changing the text (bounded), so
    nested environments/braces like ``\\textbf{\\emph{x}}`` fully unwind."""
    for _ in range(passes):
        new = pattern.sub(repl, text)
        if new == text:
            break
        text = new
    return text


def _clean_tex(text: str) -> str:
    cleaned = text or ""
    cleaned = cleaned.replace(r"\%", "\x00PCT\x00")  # protect escaped percent before comment strip
    cleaned = _sub_until_stable(_STRIP_ENV_RE, " ", cleaned)  # math/table/figure envs (nested)
    cleaned = _MATH_DISPLAY_RE.sub(" ", cleaned)  # \[…\], $$…$$
    cleaned = _MATH_PAREN_RE.sub(" ", cleaned)  # \(…\)
    cleaned = _MATH_INLINE_RE.sub(" ", cleaned)  # $…$
    cleaned = re.sub(r"%.*", "", cleaned)  # strip TeX comments
    cleaned = cleaned.replace("\x00PCT\x00", "%")
    # Drop cross-refs entirely — they add nothing to prose
    cleaned = re.sub(r"\\(?:cite[tp]?|(?:auto)?ref|label|eqref|footnotemark)\{[^}]*\}", "", cleaned)
    cleaned = re.sub(r"\\footnote\{[^}]*\}", "", cleaned)
    # \cmd[opt]{arg} → arg, repeated so nested braces unwind fully
    cleaned = _sub_until_stable(_TEX_INNER_ARG_RE, r"\1", cleaned)
    cleaned = re.sub(r"\\[a-zA-Z]+\*?\s*", " ", cleaned)  # bare commands (\alpha, \sum, …)
    cleaned = re.sub(r"\\[^a-zA-Z\s]", " ", cleaned)  # \\, \{, \&, etc.
    cleaned = cleaned.replace("{", " ").replace("}", " ")
    cleaned = re.sub(r"[ \t]*&[ \t]*", " ", cleaned)  # leftover table column separators
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _safe_extract(tar: tarfile.TarFile, target_dir: Path) -> None:
    """Extract regular files/directories into staging, with per-member checks."""
    total = 0
    for count, member in enumerate(tar, 1):
        if count > _MAX_MEMBERS:
            raise tarfile.TarError("archive has too many members")
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts or "\\" in member.name:
            raise tarfile.TarError(f"unsafe path in archive: {member.name}")
        if not (member.isfile() or member.isdir()) or member.size < 0:
            raise tarfile.TarError(f"unsupported member in archive: {member.name}")
        total += member.size
        if total > _MAX_EXTRACT_BYTES:
            raise tarfile.TarError("archive exceeds uncompressed size cap")
        tar.extract(member, target_dir, filter="data")
