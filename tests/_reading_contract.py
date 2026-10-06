"""Independent saved-input → rendered-HTML preservation checks, not scientific truth.

check_html(source, html, checks, *, evidence_path) returns per-check receipts.
Each check declares id, source_id (a nonempty tuple of JSON keys/indexes),
selector (tag/attrs or heading), mode (contains/exact/count/attribute), and
optional expected (otherwise the source value). count requires explicit expected;
attribute requires attribute. scope is an optional selector for association checks.
For example scope={"attrs": {"title": source_goal}} binds a quote to its goal;
selector={"heading": "Limitations / uncertainty"} checks a note section.
Browser callers pass captured outerHTML, not textContent. Evidence must be the
actual UTF-8 HTML file; missing/mismatched evidence blocks acceptance. Applicability
is declared by the check list, never inferred from candidate data or falsey gold.
"""

import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path


_UNSET = object()
_VOID = set("area base br col embed hr img input link meta param source track wbr".split())
_BLOCK = set("p div section li ul ol h1 h2 h3 h4 h5 h6 br blockquote details summary".split())


def canonical_text(text):
    """Only presentation whitespace changes; punctuation/case/signs remain exact."""
    return " ".join(text.split())


class Node:
    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def text(self):
        if self.tag in {"script", "style"}:
            return ""
        parts = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                value = child.text()
                parts.append(f" {value} " if child.tag in _BLOCK else value)
        return "".join(parts)

    def nodes(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.nodes()


class Document(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.root = Node("document", [])
        self.stack = [self.root]
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _select(root, selector):
    if "heading" in selector:
        result = []
        for parent in root.nodes():
            collecting = False
            section = Node("section", [])
            for child in parent.children:
                if isinstance(child, Node) and child.tag in {"h1", "h2", "h3"}:
                    if collecting:
                        result.append(section)
                    collecting = canonical_text(child.text()) == selector["heading"]
                    section = Node("section", [])
                elif collecting:
                    section.children.append(child)
            if collecting:
                result.append(section)
        return result
    return [node for node in root.nodes() if
            ("tag" not in selector or node.tag == selector["tag"]) and
            all(node.attrs.get(key) == value for key, value in selector.get("attrs", {}).items())]


def _validate_selector(selector):
    if not isinstance(selector, dict) or not selector or set(selector) - {"tag", "attrs", "heading"}:
        raise ValueError("selector needs tag, attrs or heading")
    if "attrs" in selector and (not isinstance(selector["attrs"], dict) or not selector["attrs"]):
        raise ValueError("attrs must be a nonempty mapping")
    if "heading" in selector and len(selector) != 1:
        raise ValueError("heading selector cannot be combined")
    values = list(selector.get("attrs", {}).values()) + [v for k, v in selector.items() if k != "attrs"]
    if not all(isinstance(value, str) and value for value in values):
        raise ValueError("selector values must be nonempty strings")


def _validate_checks(checks):
    if not isinstance(checks, (list, tuple)) or not checks:
        raise ValueError("nonempty declared checks required")
    ids = []
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("check must be a mapping")
        if set(check) - {"id", "source_id", "selector", "scope", "mode", "expected", "attribute"}:
            raise ValueError("unknown check fields")
        identity = check.get("id")
        path = check.get("source_id")
        if not isinstance(identity, str) or not identity.strip() or identity in ids:
            raise ValueError("check IDs must be nonempty and unique")
        ids.append(identity)
        if not isinstance(path, tuple) or not path or not all(type(p) in {str, int} for p in path):
            raise ValueError("source_id must be a nonempty key/index tuple")
        if check.get("mode", "contains") not in {"contains", "exact", "count", "attribute"}:
            raise ValueError("unknown check mode")
        _validate_selector(check.get("selector"))
        if "scope" in check:
            _validate_selector(check["scope"])
        if check.get("mode") == "count" and (type(check.get("expected")) is not int or check["expected"] < 0):
            raise ValueError("count requires declared nonnegative integer expected")
        if check.get("mode") == "attribute" and not check.get("attribute"):
            raise ValueError("attribute mode requires attribute")
        if "expected" in check and type(check["expected"]) not in {str, bool, int, float, type(None)}:
            raise ValueError("expected must be a finite JSON scalar")
        json.dumps(check.get("expected"), allow_nan=False)
    return ids


def _source_value(source, path):
    value = source
    for key in path:
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and type(key) is int and 0 <= key < len(value):
            value = value[key]
        else:
            return _UNSET
    return value


def _compare(root, check, expected):
    scopes = _select(root, check["scope"]) if "scope" in check else [root]
    if "scope" in check and len(scopes) != 1:
        return False, {"scope_matches": len(scopes)}
    nodes = [node for scope in scopes for node in _select(scope, check["selector"])]
    mode = check.get("mode", "contains")
    if mode == "count":
        return len(nodes) == expected, len(nodes)
    if mode == "attribute":
        actual = [node.attrs.get(check["attribute"]) for node in nodes]
        return actual == [expected], actual
    actual = [canonical_text(node.text()) for node in nodes]
    text = canonical_text(str(expected))
    if mode == "exact" or not text:
        return actual == [text], actual
    return any(text in value for value in actual), actual


def check_html(source, markup, checks, *, evidence_path):
    """Run field-preservation checks against independently parsed captured HTML."""
    _validate_checks(checks)
    if not isinstance(source, dict) or not isinstance(markup, str):
        raise ValueError("source mapping and HTML string required")
    json.dumps(source, allow_nan=False)
    path = Path(evidence_path)
    evidence = path.read_bytes() if path.is_file() else None
    evidence_ok = evidence is not None and evidence == markup.encode("utf-8")
    root = Document(markup).root
    receipts = []
    for check in checks:
        value = _source_value(source, check["source_id"])
        expected = check.get("expected", value)
        status, actual, error = "BLOCKED", None, "missing_source"
        if value is not _UNSET:
            passed, actual = _compare(root, check, expected)
            status, error = ("PASS", None) if passed else ("FAIL", "field_mismatch")
        if not evidence_ok:
            status, error = "BLOCKED", "missing_or_mismatched_evidence"
        receipts.append({"check_id": check["id"], "status": status,
                         "expected": None if expected is _UNSET else expected, "actual": actual,
                         "source_id": list(check["source_id"]), "error_code": error,
                         "evidence_path": str(path),
                         "evidence_sha256": hashlib.sha256(evidence).hexdigest() if evidence is not None else None})
    return receipts


def aggregate(required_ids, receipts):
    """Hard veto; missing/unknown coverage blocks, duplicates are malformed."""
    if not required_ids or any(not isinstance(key, str) or not key.strip() for key in required_ids):
        raise ValueError("nonempty required IDs needed")
    if len(set(required_ids)) != len(required_ids):
        raise ValueError("duplicate required IDs")
    by_id = {}
    for receipt in receipts:
        key = receipt.get("check_id")
        if not isinstance(key, str) or not key.strip() or key in by_id:
            raise ValueError("nonempty unique receipt IDs needed")
        by_id[key] = receipt.get("status")
    missing = sorted(set(required_ids) - set(by_id))
    unknown = sorted(set(by_id) - set(required_ids))
    statuses = [by_id.get(key, "BLOCKED") for key in required_ids]
    status = "FAIL" if "FAIL" in statuses else "BLOCKED" if (
        missing or unknown or any(s != "PASS" for s in statuses)) else "PASS"
    return {"status": status, "total": len(required_ids),
            "covered": len(set(required_ids) & set(by_id)), "missing": missing, "unknown": unknown}
