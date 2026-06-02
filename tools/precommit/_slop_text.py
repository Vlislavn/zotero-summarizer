"""Comment-layer slop scanners for the slop gate (todo-stub + comment-slop).

The two text rules target the COMMENT layer that the AST discards, so they read
comments via Python's ``tokenize`` — the offset-correct, string-immune realization
of aislop's "mask strings/comments before you regex" rule (``source-masker.ts``):
tokenize structurally separates COMMENT/STRING tokens from code, so a marker inside
an f-string or a user-facing message can never trip a comment rule.

Returns lightweight ``(line, col, rule, message)`` tuples; ``check_slop.py`` wraps
them in its ``Diagnostic`` (so this module has no dependency on that contract).

Guards encode shape, not snippets (per the aislop-detector-loop discipline): every
spare below was driven by a real in-tree false positive from the adversarial review
(tracker-linked debt, why-comments, ``Phase N`` domain milestones, ``temp``/``stub``
as ordinary nouns, multi-line rationale blocks).
"""
from __future__ import annotations

import io
import re
import tokenize

Finding = tuple  # (line: int, col: int, rule: str, message: str)

# Markers built by concatenation so this rule never flags its own source
# (aislop "self-detection avoidance"); the gate also scopes to the runtime
# package, so tools/precommit/ is never scanned — this is belt-and-suspenders.
_LEAD_MARKER = re.compile(r"^(?:TO" + r"DO|FIX" + r"ME|HACK|XXX)\b", re.IGNORECASE)
# TEMP/STUB/PLACEHOLDER are ordinary English nouns, so they only count as work
# markers when they LEAD the comment AND carry an explicit label delimiter.
_LEAD_NOUN_MARKER = re.compile(r"^(?:TEMP|STUB|PLACEHOLDER)\b\s*[:(]", re.IGNORECASE)
_TRACKER = re.compile(
    r"https?://|#\d+|\bgh-\d+\b|\b[A-Z][A-Z0-9]+-\d+\b|\b(?:issue|ticket|jira)\b",
    re.IGNORECASE,
)

# A comment whose prose merely restates the following code line.
_TRIVIAL_VERB = re.compile(
    r"^(?:import|initializ|return|check|loop|iterat|creat|updat|delet|handl|fetch|"
    r"pars|validat|process|render|remov|build|increment|decrement|append|comput|"
    r"convert|assign|declar|instantiat)(?:e|es|ed|ing|s)?\b",
    re.IGNORECASE,
)
_THIS_X = re.compile(r"^this (?:function|method|class|module|variable|loop|block|line)\b", re.IGNORECASE)
# Narrative prose that explains steps rather than reasons.
_NARRATIVE = re.compile(
    r"^(?:the (?:idea|trick|point|gist) (?:here|is)\b|first(?:,| we| the)|then we\b|"
    r"finally,? we\b|next we\b|here(?:'s| is) (?:the|a|how)\b)",
    re.IGNORECASE,
)

# --- spares (a match on any of these means the comment is NOT slop) ---
_WHY = re.compile(
    r"\b(?:because|since|otherwise|workaround|caveat|assume[ds]?|important|must|should|"
    r"ensure[ds]?|avoid|prevent[s]?|require[ds]?|guarantee[ds]?|necessary|intended|"
    r"by design|on purpose|trade-?off|however|although|though|despite|"
    r"so (?:that|the|it|we|a|you|each|they|all)\b|in order to|note|warning|reason|"
    r"but |still |the user)\b",
    re.IGNORECASE,
)
_DOC_INDICATOR = re.compile(
    r"`|https?://|\be\.g\.|\bi\.e\.|\w+\.\w+\.\w+|\([^)]*\)|\||\[[^\]]+\]",
)
_SUPPRESS = re.compile(r"^(?:noqa|type:\s*ignore|pylint:|pyright:|mypy:|ruff:|flake8:|pragma|fmt:)", re.IGNORECASE)
_SECTION = re.compile(r"^(?:part|section|phase|step|stage)\b", re.IGNORECASE)
_DECORATIVE = re.compile(r"^[-=~_*#─━]{3,}$")


def _comment_tokens(source: str) -> list[tokenize.TokenInfo]:
    """Return every COMMENT token. Tokenize errors propagate (honest, not swallowed)."""
    reader = io.StringIO(source).readline
    return [tok for tok in tokenize.generate_tokens(reader) if tok.type == tokenize.COMMENT]


def _prose(token: tokenize.TokenInfo) -> str:
    """Strip the leading ``#`` markers and surrounding whitespace from a comment."""
    return token.string.lstrip("#").strip()


def scan_todos(source: str) -> list[Finding]:
    """Flag work markers (TODO/FIXME/HACK/XXX/TEMP/STUB/PLACEHOLDER) lacking a tracker."""
    findings: list[Finding] = []
    for token in _comment_tokens(source):
        prose = _prose(token)
        if not prose or _TRACKER.search(prose):
            continue
        if _LEAD_MARKER.match(prose) or _LEAD_NOUN_MARKER.match(prose):
            row, col = token.start
            findings.append((row, col, "slop/todo-stub", f"untracked work marker: {prose[:60]}"))
    return findings


def _next_code_line(lines: list[str], comment_row: int, comment_rows: set[int]) -> str | None:
    """Return the line text immediately below ``comment_row`` if it is code, else None."""
    idx = comment_row  # 0-based index of the line *after* the comment (rows are 1-based)
    if idx >= len(lines):
        return None
    text = lines[idx]
    if not text.strip() or (idx + 1) in comment_rows:
        return None  # blank or another comment — not an immediate code echo
    return text


def scan_comments(source: str) -> list[Finding]:
    """Flag trivial (restates next line) and narrative (step-by-step) comment slop."""
    lines = source.splitlines()
    tokens = _comment_tokens(source)
    comment_rows = {tok.start[0] for tok in tokens}
    findings: list[Finding] = []
    for token in tokens:
        row, col = token.start
        prose = _prose(token)
        if not prose or _is_spared(prose):
            continue
        if _in_comment_run(row, comment_rows):
            continue  # part of a >=3-line prose block — narrative-with-context, spared
        is_inline = bool(lines[row - 1][:col].strip()) if row - 1 < len(lines) else False
        kind = _classify(prose, lines, row, comment_rows, is_inline)
        if kind is not None:
            findings.append((row, col, "slop/comment-slop", f"{kind} comment adds nothing: {prose[:50]}"))
    return findings


def _is_spared(prose: str) -> bool:
    """Return whether any shape-level guard spares this comment."""
    return bool(
        len(prose) > 60
        or _SUPPRESS.match(prose)
        or _SECTION.match(prose)
        or _DECORATIVE.match(prose)
        or _WHY.search(prose)
        or _DOC_INDICATOR.search(prose)
    )


def _in_comment_run(row: int, comment_rows: set[int]) -> bool:
    """Return whether ``row`` sits inside a contiguous run of >=3 comment lines."""
    above = (row - 1 in comment_rows) + (row - 2 in comment_rows and row - 1 in comment_rows)
    below = (row + 1 in comment_rows) + (row + 2 in comment_rows and row + 1 in comment_rows)
    straddle = (row - 1 in comment_rows) and (row + 1 in comment_rows)
    return above >= 2 or below >= 2 or straddle


def _classify(prose: str, lines: list[str], row: int, comment_rows: set[int], is_inline: bool) -> str | None:
    """Return 'trivial' / 'narrative' if the comment is slop, else None."""
    if (_TRIVIAL_VERB.match(prose) or _THIS_X.match(prose)) and not is_inline:
        if _next_code_line(lines, row, comment_rows) is not None:
            return "trivial"
    if _NARRATIVE.match(prose):
        return "narrative"
    return None
