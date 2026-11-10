"""AI TL;DR for the Today card (Todo #92, terse bullets #127): one grounded structured call.

One call over the configured ``card`` stage returns at most three labelled English
bullets the owner scans in seconds — ``New`` (what is actually new), ``Take`` (a
reusable method/protocol/prompt/eval; needs a verbatim evidence span), ``Caveat``
(overclaim / missing baseline / weak eval; a verbatim quote of the paper's own
limitation, or an ABSENCE the deterministic facts confirm). The app adds a fourth
``Code`` bullet itself from the facts (never from the model). A bullet that fails its
grounding is dropped, not padded. Judge validation counts only vs humans and the eval
protocol is how THE PAPER evaluates — both come from the deterministic facts.

The clarity/method text is capped at 50k chars (pilot2 fix). Prose cards from
``card-ai-1`` are never shown: the prompt version + schema change the cache identity.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from zotero_summarizer.services.library._grounding import quote_is_grounded
from zotero_summarizer.services.library._prompt_security import UNTRUSTED_INPUT_RULE, untrusted_input

CLARITY_CAP = 50_000  # pilot2 fix: cap the method/clarity input at 50k chars
PROMPT_VERSION = "card-ai-2"

Basis = Literal["code", "uncertainty", "external_validation", "judge_validation", "data", "other"]
LABELS = ("New", "Take", "Caveat", "Code")


class Bullet(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: Literal["New", "Take", "Caveat"]
    text: str = Field(min_length=1)
    quote: str = ""  # verbatim span: required for Take; for Caveat unless `absence` is set
    absence: Basis | None = None  # Caveat only: the missing thing, when the facts confirm it


class AIFields(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    bullets: list[Bullet] = Field(max_length=3)


_INSTRUCTION = (
    "You write a terse decision TL;DR for a researcher who BUILDS and EVALUATES LLM agent systems "
    "(evaluation harnesses, judge/rubric protocols, prompts, agent contracts/specs, safety/policy "
    "enforcement, clinical/scientific multi-agent work). Use ONLY the excerpts and the deterministic "
    "facts block; never invent. Write ENGLISH only, whatever the paper's language. Return strict JSON "
    "matching the schema: `bullets`, at most 3, in the order New, Take, Caveat, each label at most once.\n"
    "HARD LIMITS: every bullet is a telegraphic fragment of AT MOST 12 words (no 'The paper', 'This work', "
    "'we', hedging or filler; keep the key number and its setting). All bullets together AT MOST 45 words. "
    "DROP a bullet rather than pad it; fewer, sharper bullets beat three weak ones.\n"
    "- New: what is actually new, in the paper's own terms, with the key number/setting if there is one. "
    "If nothing is really new, say 'Incremental: <what>' or omit.\n"
    "- Take: ONE concrete thing the reader could reuse (a named protocol, metric, harness, prompt set, "
    "judge protocol, agent contract, dataset) — use the paper's own name. quote: a verbatim span "
    "(8-25 words) from the excerpts proving it exists. Nothing reusable -> omit the bullet.\n"
    "- Caveat: the single strongest reason to trust the paper LESS: an overclaim, a missing baseline, a "
    "weak or unvalidated evaluation, a tiny sample, a limitation that undercuts the headline. quote: a "
    "verbatim span of the paper's OWN limitation. Alternatively set `absence` (code, judge_validation, "
    "uncertainty, external_validation, data) and leave quote empty — ONLY when the facts block confirms "
    "that absence AND it matters for THIS paper (judge_validation only if the paper uses an LLM judge). "
    "Generic hedges ('LLMs may err') are not caveats -> omit.\n"
)


def _facts_block(facts: dict[str, Any]) -> str:
    code = facts.get("code") or {}
    pub = facts.get("published") or {}
    checklist = facts.get("method_checklist") or {}
    judge = pub.get("judge_validation") or {}
    lines = [
        f"code: status={code.get('status')} url={code.get('url')}",
        f"eval_protocol_described={bool((pub.get('evaluation_protocol') or {}).get('present'))} "
        f"(how THE PAPER evaluates its own results)",
        f"judge_validated_vs_humans={bool(judge.get('present'))} numbers={judge.get('numbers')}",
        f"data_released={bool((pub.get('data_released') or {}).get('present'))}",
        f"uncertainty_reported={bool((checklist.get('uncertainty') or {}).get('present'))}",
        f"external_validation={bool((checklist.get('external_validation') or {}).get('present'))}",
    ]
    return "\n".join(lines)


def build_prompt(facts: dict[str, Any], title: str, abstract: str, text: str) -> str:
    body = (text or abstract or "")[:CLARITY_CAP]
    return "\n".join([
        UNTRUSTED_INPUT_RULE,
        _INSTRUCTION,
        f"Title: {title}",
        f"Abstract: {abstract[:1500]}",
        "Deterministic facts (trusted; computed by the app, not the paper):",
        _facts_block(facts),
        "Paper excerpts (untrusted text):",
        untrusted_input(body),
    ])


# --- deterministic absence verification -------------------------------------
def _absent_bases(facts: dict[str, Any]) -> set[str]:
    """Bases whose absence the deterministic facts actually confirm."""
    code = facts.get("code") or {}
    pub = facts.get("published") or {}
    checklist = facts.get("method_checklist") or {}
    absent: set[str] = set()
    if (code.get("status") or "none") in {"none", "unclear", "unknown"}:
        absent.add("code")
    if not (pub.get("judge_validation") or {}).get("present"):
        absent.add("judge_validation")
    if not (checklist.get("uncertainty") or {}).get("present"):
        absent.add("uncertainty")
    if not (checklist.get("external_validation") or {}).get("present"):
        absent.add("external_validation")
    if not (pub.get("data_released") or {}).get("present"):
        absent.add("data")
    return absent


def _code_bullet(facts: dict[str, Any]) -> str | None:
    """Deterministic `Code:` line from the facts; nothing when no full text was read."""
    status = (facts.get("code") or {}).get("status")
    return {
        "own_live": "Own repo, live",
        "own_unverified": "Own repo linked",
        "promised": "Promised, repo dead",
        "none": "None found",
    }.get(status)


def postprocess(ai: AIFields, facts: dict[str, Any], text: str) -> tuple[dict[str, Any], dict[str, str]]:
    """Ground the model bullets, add the deterministic ``Code`` bullet, and return
    ``({"bullets": [{label, text}]}, status-per-label)`` with status ok/dropped/missing."""
    context = text or ""
    absent = _absent_bases(facts)
    by_label: dict[str, str] = {}
    status = {label: "missing" for label in LABELS}
    for item in ai.bullets:
        if item.label in by_label:
            continue
        grounded = bool(item.quote) and quote_is_grounded(item.quote, context, fuzzy=True)
        verified_absence = item.label == "Caveat" and item.absence is not None and item.absence in absent
        keep = item.label == "New" or grounded or verified_absence
        status[item.label] = "ok" if keep else "dropped"
        if keep:
            by_label[item.label] = item.text.strip()
    code = _code_bullet(facts)
    if code:
        by_label["Code"], status["Code"] = code, "ok"
    bullets = [{"label": label, "text": by_label[label]} for label in LABELS if label in by_label]
    return {"bullets": bullets}, status


__all__ = ["AIFields", "CLARITY_CAP", "LABELS", "PROMPT_VERSION", "build_prompt", "postprocess"]
