"""Field-level repair of a digest whose fields failed source grounding (#130).

A rejected digest is never thrown away for a few bad fields: a small call (no reasoning; ONE
reasoning escalation if its re-check still rejects) rewrites only the rejected fields, only those are re-verified, and a non-required field that
still fails is emptied and recorded as ``not_established`` (never shown as verified).
A required field that still fails raises the verifier's rejection as before."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from pydantic import create_model

from zotero_summarizer.models import PaperDigest
from zotero_summarizer.models.digest_generation import PaperDigestGeneration
from zotero_summarizer.services.faithbench._build_claims import CLAIM_FIELDS
from zotero_summarizer.services.library._digest_verification import (
    DigestSourceRejected, _Check, _Checks, _claims,
)
from zotero_summarizer.services.library._prompt_security import untrusted_input

# A digest without these is not a usable review (read_decision is never a verified claim).
REQUIRED_FIELDS = frozenset({'tldr', 'read_why', 'key_findings', 'original_value'})

_REASONS = {
    'numeric_preflight': 'it contains a number that is not in the paper',
    'verifier_support_not_established': 'the paper does not support it',
}

_REPAIR_INSTRUCTIONS = (
    "\n\nThe digest was checked against the paper; ONLY the fields below failed."
    "\nRejected fields with the reason (untrusted data):\n{rejected}"
    "\nRewrite just these fields from the paper text above. Verification feedback identifies errors, "
    "not new evidence. Keep every number, name and claim only if the paper states it. For a judgement, "
    "write 'premise from the paper [verbatim quote] -> implication' and mark the implication as your "
    "assessment. Use an empty value when the paper gives no premise. Return ONE JSON object with exactly "
    "these keys: {names}."
)


def field_of(claim: str) -> str:
    """Top-level digest field of a ``'path: text'`` claim (``parameters.dataset`` -> ``parameters``)."""
    return claim.partition(':')[0].split('[', 1)[0].split('.', 1)[0]


def _rejected_fields(exc: DigestSourceRejected) -> list[str]:
    fields = list(dict.fromkeys(field_of(claim) for claim in exc.rejected_claims))
    if not fields or any(field not in CLAIM_FIELDS for field in fields):
        raise exc  # nothing repairable: fail as before
    return fields


def _repair_model(fields: list[str]):
    return create_model('DigestFieldRepair', **{
        name: (PaperDigestGeneration.model_fields[name].annotation, deepcopy(PaperDigestGeneration.model_fields[name]))
        for name in fields})


def _repair_prompt(prompt: str, digest: PaperDigest, exc: DigestSourceRejected, fields: list[str]) -> str:
    reason = _REASONS.get(exc.failure_kind, _REASONS['verifier_support_not_established'])
    values = digest.model_dump(mode='json', include=set(fields))
    rejected = '\n'.join(f'{name}: {values[name]!r} (rejected: {reason})' for name in fields)
    return prompt + _REPAIR_INSTRUCTIONS.format(rejected=untrusted_input(rejected), names=', '.join(fields))


def _as_dict(values: Any) -> dict[str, Any]:
    return values if isinstance(values, dict) else values.model_dump() if hasattr(values, 'model_dump') else {}


def _parts(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _supported(claims: list[str], checks: _Checks | None) -> dict[str, _Check]:
    """Supported verdicts by claim text (a check index only means something inside one run)."""
    return {claims[c.index]: c for c in (checks.checks if checks else ()) if c.supported}


def _rebuilt(digest: PaperDigest, update: dict[str, Any], exc: DigestSourceRejected) -> PaperDigest:
    """Apply ``update`` and keep the cross-field reading contract satisfiable without the field:
    at most 3 writing_reasons; no friction level without a reason (-> None); a skim with no
    read_parts target becomes an unassessed reading decision (``''``) - never upgraded to
    ``read``, which would recommend more than the review established."""
    merged = {**digest.model_dump(), **update}
    merged['writing_reasons'] = list(merged.get('writing_reasons') or [])[:3]
    if not any(str(x).strip() for x in merged['writing_reasons']) and merged.get('writing_friction') in {'moderate', 'high'}:
        merged['writing_friction'] = None
    if merged.get('read_decision') == 'skim' and not any(str(x).strip() for x in merged.get('read_parts') or []):
        merged['read_decision'] = ''
    try:
        return type(digest).model_validate(merged)
    except ValueError:  # also pydantic.ValidationError: the digest is no longer a valid review
        raise exc from None


def _recheck(digest: PaperDigest, known: dict[str, _Check], verify: Callable[[PaperDigest, list[str]], _Checks],
             exc: DigestSourceRejected, *, last: bool) -> tuple[PaperDigest, set[str], DigestSourceRejected | None]:
    """Verify the claims not yet known. Returns ``(digest, failed fields, rejection)``; a
    rejection is returned (digest untouched) only when ``not last`` and the failed fields can
    be repaired again; otherwise failed non-required fields are emptied and a required one raises."""
    failed: set[str] = set()
    for _ in range(3):  # repaired claims -> (only if a numeric preflight hid them) the rest -> done; <= 2 verifier calls
        todo = [claim for claim in _claims(digest) if claim not in known]
        if not todo:
            return digest, failed, None
        try:
            known |= {todo[c.index]: c for c in verify(digest, todo).checks}
        except DigestSourceRejected as again:
            known |= _supported(todo, again.checks)
            failed |= {field_of(claim) for claim in again.rejected_claims}
            if not last and failed <= PaperDigestGeneration.model_fields.keys():
                return digest, failed, again
            if failed & REQUIRED_FIELDS:
                raise
            digest = _rebuilt(digest, {name: PaperDigest.model_fields[name].get_default(call_default_factory=True)
                                       for name in failed}, again)
    raise exc


def repair_digest(
    digest: PaperDigest, exc: DigestSourceRejected, *, prompt: str, call: Callable[..., Any],
    verify: Callable[[PaperDigest, list[str]], _Checks], log: dict[str, Any] | None = None,
) -> tuple[PaperDigest, _Checks, list[str]]:
    """Return ``(final digest, checks over its claims, not_established field names)``.

    ``call(prompt, model, reasoning=bool)`` is a repair call: the first attempt runs WITHOUT
    reasoning; if the re-check still rejects (or the attempt returns nothing), ONE escalation
    with reasoning rewrites only the still-rejected fields. ``verify(digest, claims)`` verifies
    only those claims. ``log`` receives ``repair_rejected`` ({field: reason code}) and
    ``repair_escalated``. Raises ``exc`` (or the later rejection) if a required field cannot
    be grounded or the digest stops being a valid review without the field."""
    fields = _rejected_fields(exc)
    touched = set(fields)
    known = _supported(_claims(digest), exc.checks)
    log = {} if log is None else log
    rejected = log.setdefault('repair_rejected', {})
    current, failed, call_error = exc, set(), None
    for last in (False, True):
        rejected.update({name: current.failure_kind for name in fields})
        log['repair_escalated'], call_error = last, None
        try:
            values = call(_repair_prompt(prompt, digest, current, fields), _repair_model(fields), reasoning=last)
        except ValueError as error:  # unparsable/invalid repair: no values; the fields are re-verified unchanged
            values, call_error = {}, error
        update = {k: v for k, v in _as_dict(values).items() if k in fields}
        if not update and not last:
            continue  # nothing to re-verify: escalate straight away
        candidate = _rebuilt(digest, update, current)
        if any(not any(str(part).strip() for part in _parts(getattr(candidate, name))) for name in REQUIRED_FIELDS & set(fields)):
            if last:
                raise (call_error or exc)  # a required field may be repaired, never blanked
            continue
        try:
            digest, failed, again = _recheck(candidate, known, verify, exc, last=last)
        except DigestSourceRejected:
            if call_error is not None and last:
                raise call_error from None  # the failed repair call is the cause: a generation failure
            raise
        if again is None:
            break
        current, fields = again, sorted(failed)
        touched |= failed
    present = {field_of(claim) for claim in _claims(digest)}
    emptied = {name for name in touched - REQUIRED_FIELDS if name not in present}
    claims = _claims(digest)
    checks = [_Check(index=i, supported=True, evidence=known[claim].evidence) for i, claim in enumerate(claims)]
    return digest, _Checks(checks=checks), sorted(failed | emptied)
