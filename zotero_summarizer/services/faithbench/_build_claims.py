"""Claims track: digest generation + atomic-claim decomposition (run stage).

The PaperDigest **is** the model-under-test's output, so claims are produced
per ``(paper, run_number)`` at run time — freezing them at build time would
benchmark a stale artifact and break honest multi-run variance. Decomposition
uses the remote builder/judge endpoint (fast, and the decomposer is not the
system being measured) and is cached per digest sha so re-runs are free.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from zotero_summarizer.services._common import extract_json_blob, to_text
from zotero_summarizer.services.faithbench._corpus import sha256_text
from zotero_summarizer.services.library import quality_review

LOGGER = logging.getLogger(__name__)

# PaperDigest fields whose content makes factual claims about the paper
# (subjective fields — read_decision, grade, dimensions — are not claims).
# read_why stays IN even though it is goal-conditioned: it can project the
# reader's goal vocabulary onto papers that never engage with it — the exact
# hallucination the product must not ship. Its claims are therefore judged
# against paper text PLUS the run's research_goals (see _judge), matching the
# generator's input, instead of being dropped or judged paper-only.
CLAIM_FIELDS = ("tldr", "read_why", "controversies", "key_strength", "key_weakness", "implementation")

_DECOMPOSE_PROMPT = (
    "Split the review snippets below into ATOMIC, self-contained factual claims "
    "about the PAPER'S CONTENT (one verifiable fact per claim; resolve pronouns; "
    "repeat the subject). DROP subjective judgments, recommendations, and "
    "statements about the reader's goals or interests. Tag every claim with the "
    "exact [field] marker of the snippet it came from.\n\n"
    "Paper title: {title}\n\nReview snippets:\n{snippets}\n\n"
    "Return ONE JSON object, nothing else: "
    '{{"claims": [{{"field": "...", "claim": "..."}}, ...]}}'
)


def digest_for_paper(
    *, title: str, full_text: str, config: Any, llm: Any
) -> tuple[dict[str, Any], str]:
    """Run the production deep-review path (``quality_review.assess_digest``)
    and return ``(digest_dump, digest_sha)``."""
    digest = quality_review.assess_digest(title=title, full_text=full_text, config=config, llm=llm)
    dump = digest.model_dump()
    sha = sha256_text(json.dumps(dump, sort_keys=True, ensure_ascii=False))
    return dump, sha


def snippets_from_digest(digest_dump: dict[str, Any]) -> dict[str, str]:
    """The claim-bearing fields, flattened to ``{field: text}`` (lists joined)."""
    out: dict[str, str] = {}
    for field in CLAIM_FIELDS:
        value = digest_dump.get(field)
        if isinstance(value, list):
            text = "; ".join(str(v) for v in value if str(v).strip())
        else:
            text = str(value or "").strip()
        if text:
            out[field] = text
    return out


def _attribute_by_overlap(claim: str, snippets: dict[str, str]) -> str:
    """The snippet field sharing the most tokens with the claim."""
    claim_tokens = set(claim.lower().split())
    return max(
        snippets,
        key=lambda f: len(claim_tokens.intersection(snippets[f].lower().split())),
    )


def decompose_digest(
    *,
    digest_dump: dict[str, Any],
    digest_sha: str,
    title: str,
    decompose_llm: Any,
    cache_dir: Path,
) -> list[dict[str, str]]:
    """``[{field, claim}, ...]`` for a digest, cached by digest sha.

    Raises on an unparseable decomposer response after one strict-JSON retry —
    without claims the claims track has nothing to judge, so this must surface.
    """
    # v2: decomposer tags each claim with its source field (was: token-overlap
    # attribution post-hoc) — keyed separately so a resumed run never mixes
    # attributions from the two schemes.
    cache_path = cache_dir / f"claims-v2-{digest_sha[:16]}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    snippets = snippets_from_digest(digest_dump)
    if not snippets:
        return []
    rendered = "\n".join(f"- [{field}] {text}" for field, text in snippets.items())
    prompt = _DECOMPOSE_PROMPT.format(title=title, snippets=rendered)
    raw = to_text(decompose_llm.prompt(prompt))
    try:
        payload = extract_json_blob(raw)
    except ValueError:
        LOGGER.warning("faithbench: claim decomposition JSON parse failed, retrying")
        retry = to_text(
            decompose_llm.prompt(
                'Return ONLY one valid JSON object {"claims": ["..."]} extracted '
                "from the following text, nothing else:\n\n" + raw
            )
        )
        payload = extract_json_blob(retry)

    # Field attribution routes judging (read_why claims are judged against
    # paper + research goals) and the per-field report breakdown. The
    # decomposer's own tag is authoritative; token overlap is the I/O-boundary
    # fallback for entries where the LLM omitted or invented the tag.
    rows: list[dict[str, str]] = []
    for entry in payload.get("claims") or []:
        if isinstance(entry, dict):
            claim = str(entry.get("claim") or "").strip()
            field = str(entry.get("field") or "").strip()
        else:
            claim, field = str(entry).strip(), ""
        if not claim:
            continue
        if field not in snippets:
            field = _attribute_by_overlap(claim, snippets)
        rows.append({"field": field, "claim": claim})

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows
