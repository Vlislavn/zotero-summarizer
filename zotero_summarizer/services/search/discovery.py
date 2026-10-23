"""Saved-seed discovery: public Search retrieval, optional consent-bound assessment."""
from copy import deepcopy
from pathlib import Path
import sqlite3

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.zotero_read import ZoteroReadError
from zotero_summarizer.services._common import settings, state
from zotero_summarizer.services.library import related, _related_assessment as semantic
from zotero_summarizer.services.library._related_context import collect_comments, fingerprint, native_detail
from zotero_summarizer.services.library.app_library_reader import AppLibraryReader, KEPT_DECISIONS
from zotero_summarizer.services.search import require_online, session as sessions
from zotero_summarizer.services.search._models import QueryPlan, ResearchSession, SearchIntent
from zotero_summarizer.services.search.dedup import _Union
from zotero_summarizer.services.search._relevance import attach_relevance
from zotero_summarizer.services.search.federate import federate
from zotero_summarizer.services.search.intent import build_query_plan
from zotero_summarizer.services.search.pipeline import _annotate_library_coverage, _search_openalex_client
from zotero_summarizer.services.search.rank import _epsilon, rank_candidates, score_query_relevance
from zotero_summarizer.services.zotero.zotero import resolve_reader_for_key
from zotero_summarizer.storage.corpus_read import _corpus_fingerprint
from zotero_summarizer.storage.library_context import read_context, read_discovery_membership

_PUBLIC_TITLE_CHARS = 500
_PUBLIC_ABSTRACT_CHARS = 3000
_PUBLIC_QUERY_CHARS = 4000  # Existing Search request/review ceiling.
_DISCOVERY_QUOTA = 15
_active_session = None  # One live synchronous owner; persisted running without it means interrupted.


def _unavailable():
    raise APIError("discovery_unverified", "Discovery inputs could not be verified", status_code=409)


def _membership(reader):
    rows = read_discovery_membership(settings().triage_db_path)
    kept = [row for row in rows if row["decision"] in KEPT_DECISIONS]
    keys, identifiers = set(), set()
    for row in kept:
        keys.update(value for value in (row["stable_feed_key"], row["materialized_zotero_key"]) if value)
        for doi, arxiv, url in ((row["doi"], row["arxiv_id"], row["url"]),
                                (row["rss_doi"], row["rss_arxiv_id"], row["canonical_url"])):
            identifiers.update(related._identifiers({"doi": doi, "arxiv_id": arxiv, "url": url}))
    native = []
    if reader is not None:
        live = {row["item_key"] for row in reader.get_all_items(include_abstract=False)["items"]}
        dois, urls = reader.get_field_values("DOI"), reader.get_field_values("url")
        native = [{"item_key": key, "identifiers": sorted(related._identifiers(
            {"doi": dois.get(key), "url": urls.get(key)}))} for key in sorted(live)]
        keys.update(live)
        identifiers.update(ident for row in native for ident in row["identifiers"])
    return rows, keys, identifiers, native, fingerprint({"kept": kept, "native": native})


def _inputs(item_key):
    related._validate_key(item_key)
    try:
        reader = state().zotero_reader
        rows, keys, identifiers, native, digest = _membership(reader)
        detail, native_state = native_detail(resolve_reader_for_key(item_key), item_key)
        if detail is None or native_state == "failed":
            _unavailable()
        families, root = _Union(), f"seed:{item_key}"
        for row in native:
            for ident in row["identifiers"]:
                families.union(f"native:{row['item_key']}", ident)
        for ident in related._identifiers(detail):
            families.union(root, ident)
        contexts, native_aliases = {}, {item_key}
        while pending := native_aliases - contexts.keys():
            contexts.update(read_context(settings().triage_db_path, sorted(pending)))
            for alias in {alias for context in contexts.values() for alias in context["aliases"]}:
                families.union(root, f"native:{alias}")
            native_aliases = {row["item_key"] for row in native
                              if families.find(f"native:{row['item_key']}") == families.find(root)}
        context = contexts[item_key]
        aliases = set(context["aliases"])
        approved = any(row["decision"] == "user_approved" and
                       {row["stable_feed_key"], row["materialized_zotero_key"]} & aliases for row in rows)
        basis = "user_approved" if approved else "native" if native_aliases else None
        context = {**context, "aliases": sorted(aliases | native_aliases)}
        comments = related._comments(item_key, detail, native_state, context, "observed",
                                     reader or AppLibraryReader(settings().triage_db_path))
        records = {record["id"]: record for record in comments["records"]}
        for alias in sorted(native_aliases - aliases):
            extra = collect_comments(alias, None, contexts[alias], "observed", "unavailable")
            records.update((record["id"], record) for record in extra["records"])
        comments["records"] = list(records.values())
        if records and comments["availability"] == "empty":
            comments["availability"] = "available"
        if any(value == "failed" for value in comments["native_sources"].values()):
            _unavailable()
        seed = related._paper(item_key, detail, comments, context)
        boundary, config = semantic.model_boundary(), state().app_state.config
        try:
            corpus = _corpus_fingerprint(str(settings().corpus_db_path))
        except FileNotFoundError:
            corpus = None
        paths = [Path(__file__), Path(semantic.__file__), Path(related.__file__),
                 Path(related.__file__).with_name("_related_context.py"),
                 Path(related.__file__).with_name("_prompt_security.py"),
                 Path(__file__).parents[2] / "storage" / "library_context.py",
                 Path(__file__).parents[2] / "runtime.py"]
        paths.extend(Path(__file__).with_name(name + ".py") for name in
                     ("_models", "intent", "_queries", "federate", "dedup", "rank", "pipeline"))
        identity = {"seed": seed, "context": contexts, "membership": digest, "basis": basis,
                    "boundary": boundary, "config": config.model_dump(mode="json"),
                    "corpus": corpus, "corpus_path": str(settings().corpus_db_path),
                    "encoder": getattr(state().embedding_cache, "_encoder_id", None),
                    "assessment": semantic.assessment_identity(), "epsilon": _epsilon(), "quota": _DISCOVERY_QUOTA,
                    "timeout": settings().summary_timeout_seconds,
                    "sources": {str(path): fingerprint(path.read_text()) for path in paths}}
        return {"seed": seed, "basis": basis, "boundary": boundary, "membership_digest": digest,
                "saved_keys": keys, "saved_identifiers": identifiers, "fingerprint": fingerprint(identity)}
    except (OSError, sqlite3.Error, ZoteroReadError) as exc:
        raise APIError("discovery_unverified", "Discovery inputs could not be read", status_code=409) from exc


def discovery_metadata(item_key: str) -> dict:
    related._validate_key(item_key)
    boundary = semantic.model_boundary()
    try:
        inputs = _inputs(item_key)
    except APIError as exc:
        if exc.error != "discovery_unverified":
            raise
        return {"seed_key": item_key, "seed_title": "", "admission": {"state": "unknown", "basis": None},
                "context_fingerprint": None, "boundary": boundary,
                "private_context": {"status": "unavailable", "record_count": None}, "reason": exc.error}
    comments = inputs["seed"]["comments"]
    status = {"available": "observed", "empty": "empty", "partial": "partial", "unavailable": "unavailable"}
    return {"seed_key": item_key, "seed_title": inputs["seed"]["title"],
            "admission": {"state": "saved" if inputs["basis"] else "not_saved", "basis": inputs["basis"]},
            "context_fingerprint": inputs["fingerprint"], "boundary": inputs["boundary"],
            "private_context": {"status": status[comments["availability"]], "record_count": len(comments["records"])},
            "reason": None if inputs["basis"] else "saved_seed_required"}


def _candidate_fingerprint(sess):
    fields = {"candidate_id", "title", "abstract", "doi", "arxiv_id", "pmid", "pmcid", "openalex_id",
              "authors", "year", "venue", "url", "is_open_access", "is_retracted", "publication_types",
              "version_type", "cited_by_count"}
    rows = [
        {**{key: value for key, value in cand.to_dict().items() if key in fields},
         "family_identifier_keys": sorted(cand.identifier_keys())}
        for cand in sess.candidates]
    return fingerprint(sorted(rows, key=lambda row: row["candidate_id"]))


def _freshness(sess):
    if not {"seed_key", "context_fingerprint", "candidate_fingerprint"} <= sess.discovery.keys():
        return "unverified"
    if sessions.is_deleted(sess.id):
        return "stale"
    try:
        inputs = _inputs(sess.discovery["seed_key"])
    except APIError as exc:
        if exc.error != "discovery_unverified":
            raise
        return "unverified"
    candidate_identity = sess.discovery["candidate_fingerprint"]
    candidates_current = (_candidate_fingerprint(sess) == candidate_identity
                          if candidate_identity is not None else not sess.candidates)
    current = inputs["basis"] and inputs["fingerprint"] == sess.discovery["context_fingerprint"] and candidates_current
    return "current" if current else "stale"


def require_current_discovery(sess: ResearchSession) -> None:
    if sess.discovery and (_freshness(sess) != "current" or sess.status not in {
            "discovery_ready", "reviewing", "reviewed", "error"}):
        raise APIError("discovery_stale", "Reload discovery before starting a new action", status_code=409)


def _check(sess, consent_token=None):
    if sessions.is_deleted(sess.id):
        raise APIError("discovery_deleted", "Discovery session was deleted", status_code=409)
    if _freshness(sess) != "current":
        raise APIError("discovery_stale", "Discovery inputs changed during work", status_code=409)
    if consent_token is not None and semantic.model_boundary()["token"] != consent_token:
        raise APIError("consent_required", "Consent to the current model is required", status_code=409)


def _retrieval(plan, observed_count, retained_count):
    variants = [{key: row[key] for key in ("source", "query", "allocation", "channel", "outcome") if key in row}
                for counts in plan.retrieval_accounting.values() for row in counts.get("variants", [])]
    statuses = [row.get("outcome", {}).get("status", "unknown") for row in variants]
    expected = sum(len(variants or ([scalar] if scalar else [])) for variants, scalar in (
        (plan.openalex_lexical_variants[:2], plan.openalex_lexical), ([], plan.openalex_semantic)))
    statuses.extend(["unknown"] * max(0, expected - len(statuses)))
    successes = {"success_empty", "success_nonempty"}
    status = "unknown"
    if statuses and all(s in successes for s in statuses):
        status = "observed"
    elif any(s in successes for s in statuses):
        status = "partial"
    elif statuses and all(s == "failed" for s in statuses):
        status = "failed"
    elif statuses and all(s == "not_attempted" for s in statuses):
        status = "not_attempted"
    empty = None
    if status == "observed" and not retained_count:
        empty = "providers_empty" if observed_count == 0 else "all_known_saved"
    return {"status": status, "empty_reason": empty, "variants": variants}


def _retrieve(sess, inputs):
    seed = inputs["seed"]
    public = "\n".join([seed["title"][:_PUBLIC_TITLE_CHARS], seed["abstract"][:_PUBLIC_ABSTRACT_CHARS],
                        *seed["identifiers"]])[:_PUBLIC_QUERY_CHARS].strip()
    if not public:
        raise APIError("seed_metadata_unavailable", "Seed metadata is required", status_code=409)
    intent = SearchIntent(raw_query=public, canonical_question=public, concepts=[seed["title"][:_PUBLIC_TITLE_CHARS]],
                          constraint_origin="user_confirmed")
    plan = build_query_plan(intent)
    for name in ("library_raw", "library_expanded", "crossref", "semantic_scholar", "openreview", "arxiv", "europepmc"):
        setattr(plan, name, "")
    plan.arxiv_variants = []
    plan.europepmc_variants = []
    sess.raw_query, sess.intent, sess.plan = public, intent, plan
    app, config = state(), state().app_state.config
    candidates = federate(plan, openalex_client=_search_openalex_client(app, config),
                          quota=_DISCOVERY_QUOTA, observe_outcomes=True)
    _annotate_library_coverage(candidates, app.zotero_reader)
    for cand in candidates:
        if (set(cand.identifier_keys()) & inputs["saved_identifiers"]
                or cand.existing_zotero_key in inputs["saved_keys"]):
            cand.in_library = True
        elif app.zotero_reader is None and any(key.startswith(("doi:", "arxiv:")) for key in cand.identifier_keys()):
            cand.in_library = False
    retained = [cand for cand in candidates if cand.in_library is not True
                and not set(cand.identifier_keys()) & set(seed["identifiers"])]
    score_query_relevance(public, retained, reranker_model=config.corpus.reranker_model)
    rank_candidates(retained)
    attach_relevance(retained)
    sess.candidates = retained[:semantic.budgets()["candidates"]]
    sess.discovery["candidate_fingerprint"] = _candidate_fingerprint(sess)
    sess.discovery["retrieval"] = _retrieval(plan, len(candidates), len(retained))


def _assess(sess, inputs, consent_token):
    if consent_token is None:
        return {"status": "not_requested", "items": [], "submitted_context_ids": []}
    with sessions._lock_for("related-assessment"):
        _check(sess, consent_token)
        seed, boundary = inputs["seed"], inputs["boundary"]
        comments = seed["comments"]
        if not boundary["enabled"]:
            result = {"status": "disabled", "items": []}
        elif not sess.candidates:
            result = {"status": "no_candidates", "items": []}
        elif not comments["records"]:
            result = {"status": "no_comments" if comments["availability"] == "empty" else "context_unavailable", "items": []}
        else:
            unavailable = {"availability": "unavailable", "app_state": "unavailable", "native_state": "unavailable", "records": []}
            pool = [related._paper(c.candidate_id, c.to_dict(), unavailable, None) for c in sess.candidates]
            result = semantic.assess(seed, pool, boundary)
        _check(sess, consent_token)
        return {**result, "submitted_context_ids": [r["id"] for r in comments["records"]]
                if result["status"] == "assessed" else []}


def _interrupted(sess):
    return (sess.status == "discovery_interrupted" or
            sess.status in {"discovery_pending", "discovery_running"} and _active_session != sess.id)


def _run(sess, inputs, consent_token, *, retrieve):
    try:
        if retrieve:
            require_online()
            if sessions.is_deleted(sess.id):
                raise APIError("discovery_deleted", "Discovery session was deleted", status_code=409)
            sess.discovery["phase"] = "retrieval"
            _retrieve(sess, inputs)
        _check(sess, consent_token)
        sess.discovery["phase"] = "assessment"
        sess.discovery["assessment"] = _assess(sess, inputs, consent_token)
        _check(sess, consent_token)
        sess.discovery["error"] = None
        failed = (sess.discovery["retrieval"]["status"] in {"failed", "not_attempted", "unknown"}
                  or sess.discovery["assessment"]["status"] == "failed")
        sess.status = "discovery_failed" if failed else "discovery_ready"
    except Exception as exc:  # noqa: BLE001 — durable typed failure, never successful empty.
        sess.status = "discovery_failed"
        code = exc.error if isinstance(exc, APIError) else "discovery_failed"
        sess.discovery["error"] = {"stage": sess.discovery["phase"], "code": code}
        if sess.discovery["phase"] == "assessment":
            sess.discovery["assessment"] = {"status": "failed", "items": [], "submitted_context_ids": []}
    sessions.save_merge(sess)
    if sessions.is_deleted(sess.id):
        raise APIError("discovery_deleted", "Discovery session was deleted", status_code=409)
    return sess


def find_new(item_key: str, *, context_fingerprint: str, consent_token: str | None = None,
             retry: bool = False) -> ResearchSession:
    global _active_session
    with sessions._lock_for("discovery"):
        inputs = _inputs(item_key)
        if not inputs["basis"] or inputs["fingerprint"] != context_fingerprint:
            raise APIError("discovery_stale", "A current saved seed is required", status_code=409)
        if consent_token is not None and consent_token != inputs["boundary"]["token"]:
            raise APIError("consent_required", "Consent to the current model is required", status_code=409)
        previous = sessions.find_discovery(context_fingerprint)
        if previous is not None:
            if _interrupted(previous):
                previous.status = "discovery_interrupted"
            if previous.status in {"reviewing", "reviewed", "error"}:
                return previous
            assessment_only = (consent_token is not None and previous.discovery["assessment"]["status"] == "not_requested"
                               and previous.status == "discovery_ready")
            if not retry and not assessment_only:
                return previous
            if (retry and previous.status == "discovery_ready"
                    and previous.discovery["assessment"]["status"] == "assessed"
                    and previous.discovery["retrieval"]["status"] == "observed"):
                return previous
        else:
            require_online()
        sess = previous or sessions.new_session(raw_query="Find new relevant", intent=SearchIntent(raw_query="Find new relevant"),
                                                plan=QueryPlan(), questions=[])
        if previous is None:
            sess.id = f"discovery_{context_fingerprint}_{sess.id}"
            sess.discovery = {"version": 1, "seed_key": item_key, "seed_title": inputs["seed"]["title"],
                              "context_fingerprint": context_fingerprint, "candidate_fingerprint": None,
                              "snapshot": inputs["seed"], "boundary": inputs["boundary"],
                              "membership_digest": inputs["membership_digest"], "attempts": [], "phase": "retrieval",
                              "retrieval": {"status": "not_attempted", "variants": [], "empty_reason": None},
                              "assessment": {"status": "not_requested", "items": [], "submitted_context_ids": []}, "error": None}
        else:
            sess.discovery["attempts"].append(deepcopy({"status": sess.status, **{key: sess.discovery[key]
                                                       for key in ("retrieval", "assessment", "error")}}))
        retrieve = previous is None or (retry and sess.discovery["retrieval"]["status"] != "observed")
        sess.discovery["assessment"] = {"status": "not_requested", "items": [], "submitted_context_ids": []}
        _active_session, sess.status = sess.id, "discovery_running"
        try:
            if previous:
                sessions.save_merge(sess)
            else:
                sessions.save(sess)
            return _run(sess, inputs, consent_token, retrieve=retrieve)
        finally:
            _active_session = None


def discovery_view(sess: ResearchSession) -> dict:
    if not sess.discovery:
        return sess.to_dict(for_display=True)
    durable, freshness = sess.discovery, _freshness(sess)
    current, interrupted = freshness == "current", _interrupted(sess)
    assessment = deepcopy(durable["assessment"])
    if not current or interrupted:
        assessment = {"status": "stale" if not current else "interrupted", "items": [], "submitted_context_ids": []}
    candidates = {c.candidate_id: c for c in sess.candidates}
    public_ids = [c.candidate_id for c in sess.candidates if c.in_library is False]
    recommended = []
    if assessment["status"] == "assessed":
        recommended = [row["item_key"] for row in assessment["items"] if row["relevance"] == "relevant"
                       and row["preference"] == "compatible" and row["item_key"] in public_ids]
    elif assessment["status"] in {"not_requested", "no_comments", "disabled"}:
        recommended = public_ids
    projection = {key: durable[key] for key in ("version", "seed_key", "seed_title", "context_fingerprint", "candidate_fingerprint", "boundary", "retrieval", "error")}
    projection.update(freshness=freshness, assessment=assessment,
                      recommended_ids=recommended[:3] if current else [],
                      unresolved_membership=[key for key, c in candidates.items() if c.in_library is None],
                      seed_comments=durable["snapshot"]["comments"] if current and not interrupted else None,
                      comments_in_ranking=current and any(row["used_context_ids"] for row in assessment["items"]),
                      new_actions_allowed=current and not interrupted and sess.status in {"discovery_ready", "reviewing", "reviewed", "error"})
    result = sess.to_dict(for_display=True)
    result["discovery"] = projection
    if interrupted:
        result["status"] = "discovery_interrupted"
    return result
