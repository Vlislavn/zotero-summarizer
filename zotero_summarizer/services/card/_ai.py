"""AI TL;DR for the Today card (Todo #92, terse bullets #127): one grounded structured call.

One call over the configured ``card`` stage returns at most three labelled English
bullets the owner scans in seconds — ``New`` (what is actually new), ``Take`` (a
reusable method/protocol/prompt/eval; needs a verbatim evidence span), ``Caveat``
(overclaim / missing baseline / weak eval; a verbatim quote of the paper's own
limitation, or an ABSENT CODE the deterministic facts confirm). The app adds a fourth
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
PROMPT_VERSION = "card-ai-3"

Basis = Literal["code", "uncertainty", "external_validation", "judge_validation", "data", "other"]
LABELS = ("New", "Take", "Caveat", "Code")


class Bullet(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: Literal["New", "Take", "Caveat"]
    text: str = Field(min_length=1)
    quote: str = ""  # verbatim span: required for Take; for Caveat unless `absence` is set
    absence: Basis | None = None  # Caveat only: the missing thing, when the facts confirm it (only `code` can be)


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
    "verbatim span of the paper's OWN limitation. Alternatively set `absence` to `code` and leave quote "
    "empty — ONLY when the facts block shows no own code AND it matters for THIS paper. Never claim any "
    "other absence from the facts block: unlisted flags are unknown, not missing. "
    "Generic hedges ('LLMs may err') are not caveats -> omit.\n"
)


def _found(group: dict[str, Any], key: str) -> bool:
    return bool((group.get(key) or {}).get("present"))


def _facts_block(facts: dict[str, Any]) -> str:
    """Trusted facts for the prompt: the code status plus only the text flags the collector FOUND.
    A regex that finds nothing proves nothing (narrow vocabulary), so no absence is ever stated."""
    code = facts.get("code") or {}
    pub = facts.get("published") or {}
    checklist = facts.get("method_checklist") or {}
    judge = pub.get("judge_validation") or {}
    lines = [f"code: status={code.get('status')} url={code.get('url')}"]
    if _found(pub, "evaluation_protocol"):
        lines.append("eval_protocol_described=True (how THE PAPER evaluates its own results)")
    if judge.get("present"):
        lines.append(f"judge_validated_vs_humans=True numbers={judge.get('numbers')}")
    if _found(pub, "data_released"):
        lines.append("data_released=True")
    if _found(checklist, "uncertainty"):
        lines.append("uncertainty_reported=True")
    if _found(checklist, "external_validation"):
        lines.append("external_validation=True")
    lines.append("(Flags not listed were not detected by a text scan: that is NOT evidence of absence.)")
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
    """Bases whose absence the deterministic facts actually confirm. Only the code cascade can:
    the text-scan flags (uncertainty, external validation, judge validation, data) can miss, so
    their absence is never confirmed."""
    code = facts.get("code") or {}
    absent: set[str] = set()
    if (code.get("status") or "none") in {"none", "unclear", "unknown"}:
        absent.add("code")
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
