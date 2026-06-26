"""Convert raw DB rows into normalized candidate dicts.

Single responsibility: parse ``shap_contribs_json``, extract authors/venue/
rationale/affinity/prestige from the payload, derive ``item_key`` and
``shap_top``. The output dict is what the allocator works with.

Fail-fast posture:

  * Corrupt ``shap_contribs_json`` -> ``ValueError`` (data-integrity bug).
  * Non-dict / non-list payload shapes -> ``ValueError``.
  * Empty / NULL payload is a *documented contract* (older rows that
    predate Phase 1.14 have no SHAP blob) — we return zeros/empty strings
    rather than raising. The boundary check passes ``shap_contribs_json``
    through json.loads strictly; everything downstream trusts the parse.

Empty-string returns for ``authors``/``venue``/``rationale`` are part of
the public SlatePaper contract (the plan says ``rationale: str  # LLM
rationale if available, else ""``). They are NOT error masking.
"""
from __future__ import annotations

import json
import math
from typing import Any

from zotero_summarizer.services._common import LOGGER
from zotero_summarizer.services.model.rank_blend import blend_scores


def parse_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Parse ``shap_contribs_json``. Empty -> stub; corrupt -> raise."""
    raw = (row.get("shap_contribs_json") or "").strip()
    if not raw:
        return {"shap": None, "aux_context": None, "summary": None}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"row id={row.get('id')!r} has corrupt shap_contribs_json: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"row id={row.get('id')!r} shap_contribs_json is not a JSON object"
        )
    return parsed


def shap_top3(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Top-3 SHAP contributions by absolute value. Empty/missing -> []."""
    shap = payload.get("shap")
    if not shap:
        return []
    if not isinstance(shap, list):
        raise ValueError("shap payload field must be a list")
    ranked = sorted(
        shap,
        key=lambda c: abs(float(c.get("contribution", 0.0) or 0.0)),
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for item in ranked[:3]:
        out.append({
            "feature": str(item.get("feature") or ""),
            "contribution": float(item.get("contribution") or 0.0),
        })
    return out


def _obj_field(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``payload[key]`` when it is a JSON object (``{}`` if absent/null)."""
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"payload.{key} must be a JSON object or null")
    return value


def _summary_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return _obj_field(payload, "summary")


def _aux_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return _obj_field(payload, "aux_context")


def row_corpus_affinity(row: dict[str, Any], payload: dict[str, Any]) -> float:
    """The dedicated column is authoritative when present.

    Older rows (pre column rollout) leave it NULL — we then read from
    aux_context if available, else 0.0. This is a documented "no data"
    contract: the diversity picker requires strictly-negative affinity, so a
    missing-affinity row naturally fails to match and the empty-role
    fallback fires (per plan).
    """
    col = row.get("corpus_affinity")
    if col is not None:
        return float(col)
    aux = _aux_dict(payload)
    if "corpus_affinity" in aux:
        return float(aux["corpus_affinity"] or 0.0)
    return 0.0


def row_goal_sim(payload: dict[str, Any]) -> float | None:
    """Goal-text similarity for the card: max over ``aux_context.goal_sims``
    (``{goal text: cosine}``, written by the gate's aux pass and refreshed by
    every slate rescore). ``None`` = signal unavailable (no goals configured,
    corpus off, or a row not yet rescored since the signal shipped) — the rank
    blend then folds the goal weight back into relevance; never a fake 0.0.

    Deliberately NOT read from ``summary.matched_goal_similarity``: that LLM-lane
    field is clamped to ``ge=0`` and embeds a full-text seed, so it is a
    different (incomparable) number than the title+abstract cosine used here.
    """
    aux = _aux_dict(payload)
    sims = aux.get("goal_sims")
    if sims is None:
        return None
    if not isinstance(sims, dict):
        raise ValueError("payload.aux_context.goal_sims must be a JSON object or null")
    vals = [float(v) for v in sims.values() if v is not None]
    return max(vals) if vals else None


def row_prestige(row: dict[str, Any], payload: dict[str, Any]) -> float:
    """Prestige in [0, 1]. Source priority (SOTA signal first):

      0. ``aux_context.citation_percentile`` — OpenAlex field+year-normalized
         citation percentile, already in [0, 1]. The robust, non-gameable signal
         (same one the Library floor uses); used verbatim when present.
      1. ``summary.prestige_score`` (LLM 1-5 scale) normalized to [0, 1].
      2. ``aux_context.max_author_h_index`` log-ratio against reference ~30.
      3. Plan says "if absent, use the row's ``composite_score`` field" —
         we normalise composite (0..5 scale) to [0, 1] as the last resort.
    """
    aux = _aux_dict(payload)
    pct = aux.get("citation_percentile")
    if pct is not None:
        return min(1.0, max(0.0, float(pct)))
    summary = _summary_dict(payload)
    prestige = summary.get("prestige_score")
    if prestige is not None:
        return float(prestige) / 5.0
    h = aux.get("max_author_h_index")
    if h is not None:
        h_val = max(0.0, float(h))
        return min(1.0, math.log1p(h_val) / math.log1p(30.0))
    # Plan-authorised final fallback: use the row's composite_score field.
    composite = row.get("composite_score")
    if composite is None:
        return 0.0
    return min(1.0, max(0.0, float(composite) / 5.0))


def _row_str(payload: dict[str, Any], *keys: str) -> str:
    """First non-empty ``summary[key]`` over ``keys``, coerced to ``str`` (else '')."""
    summary = _summary_dict(payload)
    for key in keys:
        value = summary.get(key)
        if value:
            return str(value)
    return ""


def row_authors(payload: dict[str, Any]) -> str:
    return _row_str(payload, "authors", "author")


def row_venue(payload: dict[str, Any]) -> str:
    return _row_str(payload, "prestige_venue", "venue")


def row_rationale(payload: dict[str, Any]) -> str:
    return _row_str(payload, "triage_rationale", "rationale")


def row_quality(row: dict[str, Any]) -> dict[str, Any]:
    """Parse the persisted full-text :class:`QualityReview` for the card.

    Empty ``{}`` when the row has no review yet (the documented "not in the
    top-K reviewed set" contract). Corrupt JSON raises — a data-integrity bug,
    same posture as :func:`parse_payload`.
    """
    raw = (row.get("quality_review_json") or "").strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(
            f"row id={row.get('id')!r} quality_review_json is not a JSON object"
        )
    return parsed


def _row_item_key(row: dict[str, Any]) -> str:
    """Prefer the feed item GUID, fall back to a synthetic ``row-{id}`` token.

    The slate UI uses ``processed_feed_items.id`` (a stable surrogate key)
    for verdict POSTs, but downstream Zotero plumbing wants a content-stable
    handle, hence the GUID preference.
    """
    guid = (row.get("guid") or "").strip()
    if guid:
        return guid
    row_id = row.get("id")
    if row_id is None:
        raise ValueError("processed_feed_items row missing both guid and id")
    return f"row-{int(row_id)}"


def row_abstract(row: dict[str, Any]) -> str:
    return str(row.get("abstract") or "")


def row_pub_year(row: dict[str, Any]) -> int | None:
    v = row.get("pub_year")
    return int(v) if v is not None else None


def row_top_author_h_index(payload: dict[str, Any]) -> int | None:
    """Top-author h-index from ``aux_context.max_author_h_index``.

    Returns ``None`` when OpenAlex never matched this paper. The UI uses
    this to conditionally render an `(h=42)` badge next to authors.
    """
    aux = _aux_dict(payload)
    h = aux.get("max_author_h_index")
    if h is None:
        return None
    return int(h)


def row_citation_percentile(payload: dict[str, Any]) -> float | None:
    """OpenAlex field+year-normalized citation percentile in [0, 1].

    Returns ``None`` when no OpenAlex match / citation data exists yet — the
    "Highly cited" why-chip is then simply omitted (documented no-data contract).
    """
    aux = _aux_dict(payload)
    pct = aux.get("citation_percentile")
    return float(pct) if pct is not None else None


def make_candidate(row: dict[str, Any]) -> dict[str, Any]:
    """Normalise a DB row into the candidate dict used during allocation."""
    payload = parse_payload(row)
    row_id = row.get("id")
    if row_id is None:
        raise ValueError("processed_feed_items row missing id")
    composite = row.get("composite_score")
    surprise = row.get("surprise_score")
    # NULL scores sink to the bottom of sort — that's the right semantic for
    # items without scoring (shouldn't happen for queried decisions, but
    # defensive). NOT error masking: composite/surprise are numeric signals
    # where "absent" naturally maps to 0.
    composite_f = 0.0 if composite is None else float(composite)
    surprise_f = 0.0 if surprise is None else float(surprise)
    corpus_affinity = row_corpus_affinity(row, payload)
    h_index = row_top_author_h_index(payload)
    return {
        "id": int(row_id),
        "item_key": _row_item_key(row),
        # The library item.key written at materialization — the join handle to the
        # deep_review cache (feed GUID can't key it). None until materialized.
        "materialized_zotero_key": row.get("materialized_zotero_key"),
        "title": str(row.get("title") or ""),
        "decision": str(row.get("decision") or ""),
        "composite_score": composite_f,
        "surprise_score": surprise_f,
        "corpus_affinity": corpus_affinity,
        "prestige_score": row_prestige(row, payload),
        "authors": row_authors(payload),
        "venue": row_venue(payload),
        "rationale": row_rationale(payload),
        "quality": row_quality(row),
        "shap_top": shap_top3(payload),
        "max_author_h_index": h_index,
        "feed_name": str(row.get("feed_name") or ""),
        "created_at": str(row.get("created_at") or ""),
        "abstract": row_abstract(row),
        "pub_year": row_pub_year(row),
        # Goal-text similarity (None = unavailable) + KNOWN-prestige evidence
        # ([0,1] citation percentile, None = no OpenAlex match) — the rank
        # blend's inputs. ``prestige_score`` above keeps its fallback ladder for
        # DISPLAY, but the blend must only ever see real evidence (its last-resort
        # composite/5 fallback would be circular as a ranking input).
        "goal_sim": row_goal_sim(payload),
        "citation_percentile": row_citation_percentile(payload),
        # Order key for the slate + model/diversity pickers. Assembly overwrites
        # this with the shared relevance×goal×prestige blend (rank_blend) once
        # the whole cohort is known; the composite default keeps direct callers
        # of make_candidate orderable.
        "rank_score": composite_f,
        # Reason chips are pool-relative (goal bands = cohort terciles), so
        # assembly attaches them after the cohort exists (_relevance.attach_why).
        "why": [],
    }


def dedup_keep_newest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group rows by item_key, keep the largest created_at (lex compare)."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        cand = make_candidate(row)
        key = cand["item_key"]
        existing = grouped.get(key)
        if existing is None or cand["created_at"] > existing["created_at"]:
            grouped[key] = cand
    return list(grouped.values())


def attach_quality_from_reviews(candidates: list[dict[str, Any]]) -> int:
    """The GUID↔item_key BRIDGE: populate each candidate's ``quality`` from the
    deep-review cache via its ``materialized_zotero_key`` (the library key = the
    deep_reviews.json key — the feed GUID cannot join the library-keyed cache).

    One cache read for the whole cohort (lazy cross-domain import). Returns the
    number of candidates matched. The slate then floats high-quality model picks
    up (``_allocation._pick_model``) and renders the quality chip. Emits a LOUD
    warning when reviews exist and some candidates carry a materialized key yet
    NONE match — the v1 trap was a silently always-0 join, never surface that as
    'no quality'."""
    from zotero_summarizer.services.library import deep_review  # lazy cross-domain read

    reviews = deep_review._read_all()
    materialized = 0
    matched = 0
    for cand in candidates:
        key = (cand.get("materialized_zotero_key") or "").strip()
        if not key:
            continue
        materialized += 1
        entry = reviews.get(key)
        if entry is None:
            continue
        cand["quality"] = entry.get("quality") or {}
        matched += 1
    if reviews and materialized and not matched:
        LOGGER.warning(
            "deep-review quality bridge matched 0/%d materialized candidates against "
            "%d cached reviews — GUID↔item_key join may be broken", materialized, len(reviews),
        )
    return matched


def attach_rank_scores(candidates: list[dict[str, Any]]) -> None:
    """Overwrite each candidate's ``rank_score`` IN PLACE with the shared
    relevance × goal × prestige blend (``services/model/rank_blend``) — the
    same primitive the Library queue orders by, adapted to slate candidates:
    relevance = ``composite_score``, goal = ``goal_sim`` (None folds the goal
    weight back into relevance), prestige = the KNOWN ``citation_percentile``
    only (cohort min-max makes its [0,1] scale equivalent to the library's
    [1,5] mapping order-wise; the display ladder's circular composite fallback
    is deliberately excluded)."""
    if not candidates:
        return
    keys = blend_scores(
        [c["composite_score"] for c in candidates],
        [c["goal_sim"] for c in candidates],
        [c["citation_percentile"] for c in candidates],
    )
    for cand, key in zip(candidates, keys):
        cand["rank_score"] = key


__all__ = [
    "parse_payload",
    "shap_top3",
    "make_candidate",
    "dedup_keep_newest",
    "attach_quality_from_reviews",
    "attach_rank_scores",
    "row_corpus_affinity",
    "row_goal_sim",
    "row_prestige",
    "row_authors",
    "row_venue",
    "row_rationale",
    "row_quality",
    "row_abstract",
    "row_pub_year",
    "row_citation_percentile",
]
