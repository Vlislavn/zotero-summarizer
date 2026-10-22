"""Bounded retained-source parsing and current alias families, never live state."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import time
from collections import defaultdict
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from itertools import chain
from threading import Lock

from zotero_summarizer.domain import normalize_arxiv_id, normalize_doi
from zotero_summarizer.storage.feed_identity import is_stable_feed_key, row_feed_keys

from .inputs import BudgetExceeded
from .provenance import canonical_bytes, aware_time, unique_object, strict_json

_CSV_PARSE_LOCK = Lock()

TABLE_COLUMNS = {
    "triage_results": {"id", "item_id", "reading_priority", "response_json", "created_at"},
    "user_feedback": {"id", "item_id", "signal", "original_priority", "created_at"},
    "label_verdicts": {"id", "item_key", "user_priority", "original_derived_priority", "comment", "source", "created_at"},
    "sync_changes": {"revision", "item_key", "field", "value", "changed_at", "source"},
    "review_notes": {"item_key", "note", "updated_at"},
    "processed_feed_items": {"id", "feed_item_id", "stable_feed_key", "materialized_zotero_key", "created_at"},
    "feed_key_aliases": {"old_key", "stable_feed_key"},
    "feed_key_alias_ambiguities": {"old_key", "stable_feed_keys_json"},
}
REQUIRED_TABLES = {"triage_results", "user_feedback", "label_verdicts"}
EVENT_KINDS = {"human_feedback", "label_transition", "outcome_resolved"}




def time_fields(value):
    parsed = aware_time(value)
    if parsed is not None:
        return parsed.isoformat().replace("+00:00", "Z"), "recorded_offset"
    if isinstance(value, str):
        try:
            naive = datetime.fromisoformat(value)
        except ValueError:
            return None, "invalid_time"
        if naive.tzinfo is None:
            return None, "naive_origin_unproven"
        return None, "invalid_time"
    return None, "missing_time"






def _ambiguity_candidates(raw):
    try:
        values = strict_json(raw)
    except (ValueError, TypeError, UnicodeDecodeError) as error:
        raise ValueError("invalid retained ambiguity candidates JSON") from error
    if type(values) is not list or any(
        not isinstance(value, str) or value != value.strip() or not is_stable_feed_key(value)
        for value in values
    ):
        raise ValueError("invalid retained ambiguity candidates list")
    return values


def gap(state, reason, locator=None):
    state["gaps"].append({"reason": reason, **({"locator": locator} if locator else {})})


def _record(state, channel, data, locator, guard):
    for field in ("item_key", "item_id", "old_key"):
        if field in data and (not isinstance(data[field], str) or not data[field].strip()):
            raise ValueError(f"invalid retained {field}: {channel}")
    guard.record(data)
    state["records"].append({"channel": channel, "data": data, "locator": locator})


def _read_tables(destination, manifest, guard, state):
    path = destination / manifest["sources"]["triage_db"]["path"]
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=0)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.set_progress_handler(lambda: int(guard.expired()), 1000)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, required in TABLE_COLUMNS.items():
            guard.check()
            if table not in tables:
                if table in REQUIRED_TABLES:
                    raise ValueError(f"missing required retained table: {table}")
                state["tables"][table] = {"status": "unavailable", "records": None}
                gap(state, f"unavailable_table:{table}")
                continue
            columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            if not required <= columns:
                raise ValueError(f"incompatible optional or required retained table: {table}")
            identity = "revision" if table == "sync_changes" else "id" if "id" in columns else sorted(required)[0]
            count = 0
            for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY "{identity}"'):
                data = dict(row)
                locator = {"path": manifest["sources"]["triage_db"]["path"], "table": table,
                           "row": data[identity]}
                _record(state, table, data, locator, guard)
                if table == "feed_key_alias_ambiguities":
                    values = _ambiguity_candidates(data["stable_feed_keys_json"])
                    guard.decoded(values)
                    state["records"][-1]["identity_candidates"] = values
                count += 1
            state["tables"][table] = {"status": "available", "records": count}
    guard.check()


def _json_kind(data, name):
    if type(data) is not dict:
        return "malformed"
    if name == "classifier_runs_log":
        return "valid" if isinstance(data.get("run_id"), str) and data["run_id"] else "unknown"
    if type(data.get("schema")) is not int or data.get("schema") != 1 or not isinstance(data.get("event"), str):
        return "unknown"
    if data["event"] not in EVENT_KINDS:
        return "unknown"
    if not isinstance(data.get("item_key"), str) or not data["item_key"].strip():
        return "malformed"
    for field in ("source", "surface", "ts", "comment", "gate_sha"):
        if data.get(field) is not None and not isinstance(data[field], str):
            return "malformed"
    if "stable_id" in data and type(data["stable_id"]) is not dict:
        return "malformed"
    if data["event"] in {"human_feedback", "outcome_resolved"}:
        if type(data.get("human")) is not dict or type(data.get("model")) is not dict:
            return "malformed"
        if any(not isinstance(data["human"].get(f), str) for f in ("kind", "value")):
            return "malformed"
    else:
        if not {"previous_user_priority", "new_user_priority", "history_known"} <= data.keys():
            return "malformed"
        if type(data["history_known"]) is not bool:
            return "malformed"
        if any(data.get(f) is not None and not isinstance(data[f], str)
               for f in ("previous_user_priority", "new_user_priority", "model_priority")):
            return "malformed"
    return "valid"


def _read_jsonl(path, name, source, guard, state):
    with path.open("rb") as handle:
        start = 0
        lines = iter(lambda: handle.readline(guard.budgets.max_line_bytes + 1), b"")
        for number, raw in enumerate(lines, 1):
            guard.line(raw)
            locator = {"path": source["path"], "line": number, "byte_start": start, "byte_end": start + len(raw)}
            start += len(raw)
            source["records"] += 1
            guard.record(raw)
            if not raw.endswith(b"\n"):
                source["malformed"] += 1
                gap(state, "torn_record", locator)
                continue
            try:
                data = strict_json(raw)
                canonical_bytes(data)
            except (ValueError, UnicodeDecodeError):
                source["malformed"] += 1
                gap(state, "malformed_record", locator)
                continue
            classification = _json_kind(data, name)
            if classification != "valid":
                source[classification] += 1
                gap(state, classification + "_record", locator)
                continue
            guard.decoded(data)
            state["records"].append({"channel": name, "data": data, "locator": locator})


def _check_csv_limit(ceiling):
    if csv.field_size_limit() != ceiling:
        raise RuntimeError("CSV field limit changed externally; ambient restoration unavailable")


@contextmanager
def _csv_field_limit(ceiling, guard):
    """Serialize owned calls only; unrelated CSV consumers require process isolation."""
    if not _CSV_PARSE_LOCK.acquire(timeout=max(0.0, guard.deadline - time.monotonic())):
        raise BudgetExceeded("analysis safety wall cap exhausted waiting for CSV parser lock")
    try:
        guard.check()
        previous = csv.field_size_limit()
        try:
            csv.field_size_limit(ceiling)
            yield
        finally:
            _check_csv_limit(ceiling)  # observed foreign setting is preserved, not overwritten
            csv.field_size_limit(previous)
    finally:
        _CSV_PARSE_LOCK.release()


def _next_csv_row(reader, ceiling):
    _check_csv_limit(ceiling)
    try:
        return next(reader)
    finally:
        _check_csv_limit(ceiling)


def _csv_lines(raw, guard, position):
    ends = (match.end() for match in re.finditer(rb"\r\n|\r|\n", raw))
    for end in chain(ends, (len(raw),)):
        start = position["byte_end"]
        if start == end:
            continue
        line = raw[start:end]
        guard.line(line)
        position["line"] += 1
        position["byte_end"] = end
        yield line


def _read_csv(path, source, guard, state):
    raw = path.read_bytes()
    guard.decoded(raw)
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        position = {"line": 0, "byte_end": 0}
        for _line in _csv_lines(raw, guard, position):
            guard.check()
        guard.record(raw)
        source["malformed"] += 1
        gap(state, "malformed_csv_encoding", {"path": source["path"], "line": 1, "line_end": position["line"]})
        return
    # UTF-8 field characters cannot outnumber the already budget-checked raw bytes.
    ceiling = guard.budgets.max_decoded_bytes
    with _csv_field_limit(ceiling, guard):
        _csv_rows(raw, source, guard, state)


def _csv_rows(raw, source, guard, state):
    ceiling = guard.budgets.max_decoded_bytes
    position = {"line": 0, "byte_end": 0}
    lines = _csv_lines(raw, guard, position)
    reader = csv.reader((line.decode("utf-8") for line in lines), strict=True)
    previous = previous_byte = 0
    fields = None
    while True:
        try:
            row = _next_csv_row(reader, ceiling)
        except StopIteration:
            break
        except csv.Error:
            for _line in lines:
                guard.check()
            source["records"] += 1
            source["malformed"] += 1
            gap(state, "malformed_csv_record", {"path": source["path"], "line": previous + 1,
                                               "line_end": position["line"], "byte_start": previous_byte,
                                               "byte_end": len(raw)})
            guard.record(raw[previous_byte:])
            break
        guard.csv_fields(row)
        end = reader.line_num
        locator = {"path": source["path"], "line": previous + 1, "line_end": end,
                   "byte_start": previous_byte, "byte_end": position["byte_end"]}
        previous = end
        previous_byte = position["byte_end"]
        guard.record(row)
        if fields is None:
            fields = row
            if "item_key" not in fields or len(fields) != len(set(fields)) or any(not f for f in fields):
                source["malformed"] += 1
                gap(state, "malformed_csv_header", locator)
                fields = []
            continue
        source["records"] += 1
        if not fields or len(row) != len(fields) or not row[fields.index("item_key")].strip():
            source["malformed"] += 1
            gap(state, "malformed_csv_record", locator)
            continue
        data = dict(zip(fields, row))
        guard.decoded(data)
        state["records"].append({"channel": "golden_csv", "data": data, "locator": locator})
    if fields is None:
        source["malformed"] += 1
        gap(state, "missing_csv_header", {"path": source["path"], "line": 1})


def load_history(destination, manifest, guard):
    state = {"records": [], "tables": {}, "sources": {}, "gaps": []}
    for name, source in manifest["sources"].items():
        guard.source(source["byte_size"])
        state["sources"][name] = {"status": source["status"], "path": source["path"], "records": 0,
                                  "malformed": 0, "unknown": 0}
    _read_tables(destination, manifest, guard, state)
    state["sources"]["triage_db"]["records"] = len(state["records"])
    for name in ("golden_csv", "interaction_log", "classifier_runs_log"):
        source = state["sources"][name]
        if source["status"] == "missing":
            gap(state, "missing_" + name)
            continue
        path = destination / source["path"]
        if name == "golden_csv":
            _read_csv(path, source, guard, state)
        else:
            _read_jsonl(path, name, source, guard, state)
    return state


class Families:
    """Reuse review_notes' edge rules once, quarantining conflicting numeric IDs."""

    def __init__(self, records, guard):
        self.parents = {}
        self.ambiguous = set()
        self.content_keys = defaultdict(set)
        self.item_times = defaultdict(list)
        self.locators = defaultdict(list)
        candidates = defaultdict(set)
        edges = []
        for record in records:
            guard.check()
            data, channel = record["data"], record["channel"]
            if channel == "feed_key_alias_ambiguities":
                self.ambiguous.add(data["old_key"])
                candidates[data["old_key"]].update(record["identity_candidates"])
                self.locators[data["old_key"]].append(record["locator"])
            elif channel == "feed_key_aliases":
                candidates[data["old_key"]].add(data["stable_feed_key"])
                self.locators[data["old_key"]].append(record["locator"])
            elif channel == "processed_feed_items":
                keys = row_feed_keys(data)
                stable = keys[0] if keys and is_stable_feed_key(keys[0]) else data.get("materialized_zotero_key")
                if stable:
                    if type(data["feed_item_id"]) is int and data["feed_item_id"] > 0:
                        candidates["feed:" + str(data["feed_item_id"])].add(stable)
                    edges.append(("processed:" + str(data["id"]), stable))
                    if data.get("materialized_zotero_key"):
                        edges.append((stable, data["materialized_zotero_key"]))
                    self._content(data, stable)
                    self.item_times[stable].append(data.get("created_at"))
                for key in keys + ([data["materialized_zotero_key"]] if data.get("materialized_zotero_key") else []):
                    self.locators[key].append(record["locator"])
        self.ambiguous.update(key for key, values in candidates.items() if len(values) != 1)
        self.candidates = candidates
        for key, values in candidates.items():
            if key not in self.ambiguous:
                edges.append((key, next(iter(values))))
        for a, b in edges:
            guard.check()
            if a not in self.ambiguous and b not in self.ambiguous:
                self._union(a, b)
        self.mapping = {}
        grouped = defaultdict(list)
        for key in self.parents:
            grouped[self._find(key)].append(key)
        for keys in grouped.values():
            canonical = min(keys, key=lambda k: (not is_stable_feed_key(k), k.startswith(("feed:", "processed:")), k))
            for key in keys:
                self.mapping[key] = canonical
        for content, keys in self.content_keys.items():
            guard.check()
            targets = {self.mapping.get(key, key) for key in keys}
            self.content_keys[content] = next(iter(targets)) if len(targets) == 1 else None

    def _content(self, data, key):
        for field, normalize in (("doi", normalize_doi), ("arxiv_id", normalize_arxiv_id)):
            value = normalize(data.get(field) or "")
            if value:
                self.content_keys[(field, value)].add(key)

    def _find(self, key):
        self.parents.setdefault(key, key)
        root = key
        while self.parents[root] != root:
            root = self.parents[root]
        while self.parents[key] != key:
            following = self.parents[key]
            self.parents[key] = root
            key = following
        return root

    def _union(self, a, b):
        a, b = self._find(a), self._find(b)
        if a != b:
            self.parents[max(a, b)] = min(a, b)

    def resolve(self, key, stable_id=None):
        if key in self.ambiguous:
            return key, "ambiguous"
        canonical = self.mapping.get(key, key)
        matches = set()
        for field, normalize in (("doi", normalize_doi), ("arxiv", normalize_arxiv_id)):
            value = (stable_id or {}).get(field)
            if isinstance(value, str) and value:
                stored_field = "arxiv_id" if field == "arxiv" else field
                content = (stored_field, normalize(value))
                if content in self.content_keys:
                    target = self.content_keys[content]
                    if target is None:
                        return key, "conflicting_stable_id"
                    matches.add(target)
        if matches and (len(matches) != 1 or (key in self.mapping and canonical not in matches)):
            return key, "conflicting_stable_id"
        if matches and key not in self.mapping:
            canonical = next(iter(matches))
        return canonical, "resolved" if key in self.mapping or matches else "unlinked"


def content_group(data):
    return hashlib.sha256(canonical_bytes(data)).hexdigest()
