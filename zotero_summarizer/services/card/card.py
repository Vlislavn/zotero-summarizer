"""Today AI card (Todo #92): facts + grounded AI fields over the ``card`` stage.

The card is the facts collector (:mod:`services.card.facts`) plus the AI fields
(:mod:`services.card._ai`), gated by the EXISTING processing-boundary consent
(:mod:`services.card._consent`, the same ``ResolvedStage.fingerprint`` token the
review stages use). WITHOUT consent (or AI disabled / offline) the card is
facts-only with ``ai_available=False`` and a reason — never a blocked model call.

Generation is single-paper and idempotent (a fresh cached card is returned as-is),
bounded by the per-paper call cap, the daily token ledger and the circuit breaker
in :mod:`services.card._store`. The read path NEVER calls the model beyond facts.
Background generation for Today's top papers lives in
:mod:`services.card.background`.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import LOGGER, state
from zotero_summarizer.services.card import _ai, _consent, _store
from zotero_summarizer.services.card.facts import PaperRef, collect_facts, resolve_ref

CARD_VERSION = "card-v5-1"


def _prompt_hash() -> str:
    payload = json.dumps(
        {"v": _ai.PROMPT_VERSION, "schema": _ai.AIFields.model_json_schema(), "cap": _ai.CLARITY_CAP},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def generation_identity(model: str) -> str:
    """Stable cache identity: card_version + prompt hash + model (Todo #92)."""
    return hashlib.sha256(f"{CARD_VERSION}:{_prompt_hash()}:{model}".encode()).hexdigest()


def _public_boundary(boundary: dict[str, Any]) -> dict[str, Any]:
    return {k: boundary[k] for k in ("provider", "model", "endpoint", "local", "token", "enabled")}


def _facts_only(facts: dict[str, Any], boundary: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "stable_key": facts.get("stable_key"), "card_version": CARD_VERSION,
        "ai_available": False, "ai_reason": reason, "ai": None, "ai_status": {},
        "facts": facts, "boundary": _public_boundary(boundary),
    }


def read_card(key: str, **ref_kwargs: Any) -> dict[str, Any]:
    """Cached AI card if fresh, else a facts-only card. NEVER calls the model."""
    ref = resolve_ref(key, **ref_kwargs)
    boundary = _consent.card_boundary()
    cached = _store.load_fresh(ref.stable_key, generation_identity(boundary["model"]))
    if cached and cached.get("ai_available"):
        return cached
    facts = collect_facts(ref)
    reason = _consent.unavailable_reason(boundary)
    return _facts_only(facts, boundary, reason if reason != "ok" else "not_generated")


def cached_bullets(keys: list[str]) -> dict[str, list[dict[str, str]]]:
    """``{stable_key: TL;DR bullets}`` for keys with a fresh cached AI card. Cache-only:
    no facts collection, no model call (the Today shelf must stay cheap)."""
    identity = generation_identity(_consent.card_boundary()["model"])
    found = {}
    for key in keys:
        record = _store.load_fresh(key, identity)
        bullets = ((record or {}).get("ai") or {}).get("bullets") if (record or {}).get("ai_available") else None
        if bullets:
            found[key] = bullets
    return found


def generate_card(key: str, *, consent_token: str | None = None, force: bool = False,
                  **ref_kwargs: Any) -> dict[str, Any]:
    """Generate (or return the idempotent cached) AI card for one paper, respecting
    consent and all spend guards. A ``consent_token`` records durable consent."""
    ref = resolve_ref(key, **ref_kwargs)
    boundary = _consent.record_consent(consent_token) if consent_token else _consent.card_boundary()
    identity = generation_identity(boundary["model"])
    facts = collect_facts(ref)
    reason = _consent.unavailable_reason(boundary)
    if reason != "ok":
        return _facts_only(facts, boundary, reason)
    cached = _store.load_fresh(ref.stable_key, identity)
    if cached and cached.get("ai_available") and not force:
        return cached
    blocked = _store.spend_blocked()
    if blocked:
        return cached or _facts_only(facts, boundary, blocked)
    if not _store.can_attempt(ref.stable_key, identity):
        return cached or _facts_only(facts, boundary, "call_cap")
    return _run_generation(ref, facts, boundary, identity)


def _run_generation(ref: PaperRef, facts: dict[str, Any], boundary: dict[str, Any],
                    identity: str) -> dict[str, Any]:
    from zotero_summarizer.services.card._fulltext import get_fulltext
    from zotero_summarizer.services.llm import factory

    attempt = _store.attempts(ref.stable_key, identity) + 1
    try:
        text = get_fulltext(ref).text or ""
        prompt = _ai.build_prompt(facts, ref.title, "", text)
        client = factory.resolve_stage_client(state(), _consent.STAGE, expected_identity=boundary["token"])
        parsed = client.pydantic_prompt(prompt=prompt, pydantic_model=_ai.AIFields)
        ai = _ai.AIFields.model_validate(parsed)
        fields, status = _ai.postprocess(ai, facts, text)
        tokens = len(prompt) // 4 + len(json.dumps(parsed, ensure_ascii=False, default=str)) // 4
        _store.add_tokens(tokens)
        _store.record_success()
        card = {
            "stable_key": ref.stable_key, "card_version": CARD_VERSION, "identity": identity,
            "ai_available": True, "ai_reason": "ok", "ai": fields, "ai_status": status,
            "facts": facts, "boundary": _public_boundary(boundary), "model": boundary["model"],
            "generated_at": int(time.time()), "attempts": attempt, "tokens": tokens,
        }
        _store.save(ref.stable_key, card)
        return card
    except APIError:
        raise  # consent / credential / boundary errors surface to the caller
    except Exception as exc:  # noqa: BLE001 — model/transport failure degrades to facts-only
        LOGGER.info("card generate failed for %s: %s", ref.stable_key, exc)
        _store.record_failure()
        failed = _facts_only(facts, boundary, "generation_failed")
        failed["identity"] = identity
        failed["attempts"] = attempt
        _store.save(ref.stable_key, failed)
        return failed


__all__ = ["CARD_VERSION", "cached_bullets", "generate_card", "generation_identity", "read_card"]
