"""Saved-library connections; metadata default, explicit private-context experiment."""
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import settings, state
from zotero_summarizer.services.library import _related_assessment as semantic
from zotero_summarizer.services.library._related_context import (
    app_context, collect_comments, fingerprint, native_detail,
)
from zotero_summarizer.services.library._search import hybrid_search
from zotero_summarizer.services.library.app_library_reader import AppLibraryReader
from zotero_summarizer.services.search import session as sessions
from zotero_summarizer.services.search._models import Candidate, QueryPlan, SearchIntent
from zotero_summarizer.services.zotero.zotero import get_library_reader, resolve_reader_for_key
from zotero_summarizer.storage.corpus_read import _corpus_fingerprint
from zotero_summarizer.storage.feed_identity import is_stable_feed_key


def _validate_key(key):
    if not is_stable_feed_key(key) and not (len(key) == 8 and key.isascii() and key.isalnum()):
        raise APIError("validation_error", "Expected a Zotero or stable feed paper key", status_code=422)


def _identifiers(row):
    parsed = urlsplit(str(row.get("url") or ""))
    doi = row.get("doi") or (parsed.path.lstrip("/") if parsed.hostname == "doi.org" else "")
    arxiv = row.get("arxiv_id") or (row.get("url") if parsed.hostname == "arxiv.org" else "")
    return set(Candidate(title=row.get("title", ""), doi=doi, arxiv_id=arxiv).identifier_keys())


def _paper(key, row, comments, context):
    verdict = (context or {}).get("verdict")
    human = verdict if verdict and verdict.get("source") == "user" and verdict.get("value") else None
    return {"item_key": key, "title": row.get("title") or "", "abstract": row.get("abstract") or "",
            "identifiers": sorted(_identifiers(row)), "comments": comments, "human_verdict": human}


def _comments(key, detail, native, context, app, reader):
    comments = collect_comments(key, detail, context, app, native)
    sources = {str((detail or {}).get("item_key") or key): native}
    for alias in (context or {}).get("aliases", []):
        if alias in sources or alias.startswith("feed:") or isinstance(reader, AppLibraryReader):
            continue
        other, status = native_detail(reader, alias)
        extra = collect_comments(alias, other, None, app, status)
        comments["records"].extend(extra["records"])
        comments["excluded_owned_notes"].extend(extra["excluded_owned_notes"])
        sources[alias] = status
    native_states = {value for name, value in sources.items() if not name.startswith("feed:")}
    if native_states:
        comments["native_state"] = next(iter(native_states)) if len(native_states) == 1 else "partial"
        complete = app == "observed" and native_states == {"observed"}
        comments["availability"] = ("available" if comments["records"] else "empty") if complete else "partial" if comments["records"] else "unavailable"
    return {**comments, "native_sources": sources}


def _snapshot(item_key, collection, tag):
    _validate_key(item_key)
    reader = get_library_reader()
    if isinstance(reader, AppLibraryReader) and (collection or tag):
        raise APIError("scope_unavailable", "Collection/tag scopes require Zotero", status_code=409)
    seed_reader = resolve_reader_for_key(item_key)
    seed, native = native_detail(seed_reader, item_key)
    if seed is None:
        raise APIError("not_found" if native == "unknown" else "source_unavailable",
                       "Seed paper could not be read", status_code=404 if native == "unknown" else 503)
    items = {row["item_key"]: row for row in reader.get_all_items(
        collection_key=collection or None, tag=tag or None, include_abstract=True)["items"]}
    contexts, app = app_context(settings().triage_db_path, list(dict.fromkeys([item_key, *items])))
    if app != "observed":
        raise APIError("source_identity_unavailable", "Cannot determine seed aliases or human exclusions while app context is unavailable", status_code=503)
    aliases = set(contexts.get(item_key, {}).get("aliases", [item_key]))
    seen_ids, seen_aliases = _identifiers(seed), set(aliases)
    candidates, excluded = {}, []
    for key, row in items.items():
        family = set(contexts.get(key, {}).get("aliases", [key]))
        ids = _identifiers(row)
        if family & seen_aliases or ids & seen_ids:
            continue
        verdict = contexts.get(key, {}).get("verdict")
        if verdict and verdict.get("source") == "user" and verdict.get("value") == "dont_read":
            excluded.append(key)
            continue
        candidates[key] = row
        seen_aliases.update(family)
        seen_ids.update(ids)
    query = "\n".join(str(seed.get(field) or "") for field in ("title", "abstract"))
    if not query.strip():
        raise APIError("seed_metadata_unavailable", "Seed title/abstract are unavailable for retrieval", status_code=409)
    ordered, scores, search = hybrid_search(query, list(candidates), top_n=semantic.budgets()["candidates"])
    pool, detail_ids = [], set(_identifiers(seed))
    for key in ordered:
        detail, availability = native_detail(reader, key)
        row = {**candidates[key], **(detail or {})}
        # Detail identifiers can reveal an alias absent from a list projection.
        ids = _identifiers(row)
        if ids & detail_ids:
            continue
        detail_ids.update(ids)
        comments = _comments(key, detail, availability, contexts.get(key), app, reader)
        pool.append(_paper(key, row, comments, contexts.get(key)))
    seed_paper = _paper(item_key, seed, _comments(item_key, seed, native, contexts.get(item_key), app, reader), contexts.get(item_key))
    return {"seed": seed_paper, "pool": pool, "library_identity": fingerprint({"items": items, "contexts": contexts}),
            "search": search, "scores": scores, "excluded": excluded,
            "scope": {"collection": collection, "tag": tag}}


def _baseline(snapshot):
    rows = [{**row, "score": snapshot["scores"].get(row["item_key"]),
             "relationship": "Metadata-search similarity; a concrete relationship is unverified.",
             "source_basis": "abstract_only" if row["abstract"] and snapshot["seed"]["abstract"] else "title_only"}
            for row in snapshot["pool"][:3]]
    unavailable = snapshot["search"].get("semantic_unavailable")
    boundary = semantic.model_boundary()
    return {"seed_key": snapshot["seed"]["item_key"], "seed_comments": snapshot["seed"]["comments"],
            "status": "unavailable" if unavailable else "ready" if rows else "empty", "items": rows,
            "search_status": snapshot["search"], "scope": snapshot["scope"],
            "excluded_human_dont_read": snapshot["excluded"], "comments_in_ranking": False,
            "qualification": "experimental_unqualified", "boundary": boundary,
            "context_fingerprint": _identity(snapshot, boundary)}


def related_papers(item_key, *, collection="", tag=""):
    return _baseline(_snapshot(item_key, collection, tag))


def _identity(snapshot, boundary):
    config = state().app_state.config
    try:
        corpus = _corpus_fingerprint(str(settings().corpus_db_path))
    except FileNotFoundError:
        corpus = None
    paths = [Path(__file__), Path(semantic.__file__), Path(__file__).with_name("_related_context.py"),
             Path(__file__).with_name("_search.py"), Path(__file__).with_name("_prompt_security.py")]
    import zotero_summarizer.storage.library_context as context_owner
    paths.append(Path(context_owner.__file__))
    return fingerprint({"snapshot": snapshot, "corpus": corpus, "boundary": boundary,
                        "encoder": getattr(getattr(state(), "embedding_cache", None), "_encoder_id", None),
                        "config": config.model_dump(mode="json"), "assessment": semantic.assessment_identity(),
                        "timeout": settings().summary_timeout_seconds,
                        "sources": {path.name: fingerprint(path.read_text()) for path in paths}})


def _finish(snapshot, assessment):
    candidates = {row["item_key"]: row for row in snapshot["pool"]}
    selected = []
    for row in assessment["items"]:
        if row["relevance"] != "relevant" or row["preference"] != "compatible":
            continue
        paper = candidates[row["item_key"]]
        selected.append({**paper, "relationship": row["sentence"], "evidence": row["evidence"],
                         "source_basis": "metadata_only_model_judgment", "used_context_ids": row["used_context_ids"]})
    return {**_baseline(snapshot), "status": "ready" if selected else "empty", "items": selected[:3],
            "comments_in_ranking": any(row["used_context_ids"] for row in assessment["items"]),
            "assessment": assessment, "submitted_context_ids": [record["id"] for paper in [snapshot["seed"], *snapshot["pool"]]
             for record in paper["comments"]["records"]]}


def assess_related(item_key, *, consent_token, collection="", tag="", retry=False):
    # Reuse session ownership, claim and atomic publication; no second store/worker.
    # Global experiment lock also serializes distinct seeds' model work in this process.
    with sessions._lock_for("related-assessment"):
        boundary = semantic.model_boundary()
        if not boundary["enabled"] or consent_token != boundary["token"]:
            raise APIError("consent_required", "Consent to the current review model is required", status_code=409)
        snapshot = _snapshot(item_key, collection, tag)
        identity = _identity(snapshot, boundary)
        session_id = "related-" + identity
        try:
            previous = sessions.load(session_id)
        except FileNotFoundError:
            previous = None
        if previous is not None and (previous.related["context_fingerprint"] != identity or previous.status not in {
                "related_pending", "related_running", "related_complete", "related_failed", "related_interrupted"}):
            raise ValueError("Invalid persisted related-assessment identity/status")
        if previous is not None and (not retry or (previous.status == "related_complete"
                                                  and previous.related["result"]["assessment_status"] == "assessed")):
            if previous.status in {"related_pending", "related_running"}:
                result = {**_baseline(snapshot), "assessment_status": "interrupted", "session_id": session_id}
                previous.related.setdefault("prior_attempts", []).append({"status": previous.status, "result": None})
                previous.status, previous.related["result"] = "related_interrupted", result
                sessions.save(previous)
                return result
            if not isinstance(previous.related["result"], dict):
                raise ValueError("Missing terminal related-assessment result")
            return previous.related["result"]
        sess = sessions.new_session(raw_query="Related saved papers", intent=SearchIntent(raw_query="Related saved papers"),
                                    plan=QueryPlan(), questions=[])
        sess.id, sess.status = session_id, "related_pending"
        sess.related = {"seed_key": item_key, "context_fingerprint": identity, "boundary": boundary,
                        "snapshot": snapshot, "result": None,
                        "prior_attempts": ([*previous.related.get("prior_attempts", []),
                                            {"status": previous.status, "result": previous.related["result"]}]
                                           if previous is not None else [])}
        sessions.save(sess)
        if not sessions.claim(session_id, expect="related_pending", to="related_running"):
            raise APIError("assessment_busy", "Related assessment is already claimed", status_code=409)
        started = perf_counter()
        try:
            papers = [snapshot["seed"], *snapshot["pool"]]
            if not snapshot["pool"]:
                assessment = {"status": "no_candidates", "items": []}
            elif not any(paper["comments"]["records"] for paper in papers):
                observed_empty = all(paper["comments"]["availability"] == "empty" for paper in papers)
                assessment = {"status": "no_comments" if observed_empty else "context_unavailable", "items": []}
            else:
                assessment = semantic.assess(snapshot["seed"], snapshot["pool"], boundary)
            result = _finish(snapshot, assessment) if assessment["status"] == "assessed" else _baseline(snapshot)
            latest = _snapshot(item_key, collection, tag)
            if _identity(latest, semantic.model_boundary()) != identity:
                result, assessment = _baseline(latest), {"status": "stale", "items": []}
            result = {**result, "boundary": boundary, "assessment_status": assessment["status"], "session_id": session_id,
                      "context_fingerprint": identity, "elapsed_seconds": perf_counter() - started,
                      "assessment_receipt": {key: value for key, value in assessment.items() if key != "items"}}
        except Exception as exc:  # noqa: BLE001 — terminal result, never success-on-error
            if sessions.is_deleted(session_id):
                raise APIError("assessment_cancelled", "Assessment session was deleted", status_code=409) from exc
            result = {**_baseline(snapshot), "boundary": boundary, "assessment_status": "failed", "session_id": session_id,
                      "error_type": type(exc).__name__, "error_code": exc.error if isinstance(exc, APIError) else "assessment_failed",
                      "failure_stage": "context_assessment", "context_fingerprint": identity}
            sess.status, sess.related["result"] = "related_failed", result
            sessions.save(sess)
            return result
        sess.status, sess.related["result"] = "related_complete", result
        sessions.save(sess)
        if sessions.is_deleted(session_id):
            raise APIError("assessment_cancelled", "Assessment session was deleted", status_code=409)
        return result
