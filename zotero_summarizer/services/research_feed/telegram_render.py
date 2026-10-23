"""Pure news projection; confirmed cards never absorb uncertainty or failures."""
from __future__ import annotations

from zotero_summarizer.models.research_feed import TelegramNewsVerdict


COMPLETED = frozenset({"keep", "drop", "uncertain"})


def record(row: dict, identity: dict | None, *, collect_only: bool) -> dict:
    post, evaluation = row["post"], row["evaluation"]
    result = {"source_id": post.source_id, "post": post.model_dump(mode="json"),
              "content_hash": row["content_hash"], "state": "pending", "ml": None}
    if post.skip_reason:
        result.update(state="skipped", reason=post.skip_reason)
    elif collect_only or evaluation is None:
        result["reason"] = "not_evaluated"
    elif evaluation["identity"] != identity or evaluation["content_hash"] != row["content_hash"]:
        result["reason"] = "evaluation_identity_changed"
    else:
        state = evaluation["state"]
        if state not in COMPLETED | {"pending", "error"}:
            raise ValueError("unknown stored Telegram evaluation state")
        result.update(state=state, ml=evaluation["ml"], evaluation_identity=identity)
        if state in COMPLETED:
            verdict = TelegramNewsVerdict.model_validate(evaluation["verdict"])
            if verdict.decision != state or verdict.evidence_quote not in post.text:
                raise ValueError("stored verdict does not match its original post")
            result["verdict"] = verdict.model_dump(mode="json")
        else:
            result.update(reason=evaluation["reason"], error=evaluation.get("error"))
    return result


def payload(rows: list[dict], metadata: dict, identity: dict | None, *,
            card_budget: int, collect_only: bool) -> dict:
    records = [record(row, identity, collect_only=collect_only) for row in rows]
    kept = [row for row in records if row["state"] == "keep"]
    cards = [{"source_id": row["source_id"], "permalink": row["post"]["permalink"],
              **row["verdict"]} for row in kept[:card_budget]]
    uncertain = [row for row in records if row["state"] == "uncertain"]
    counts = metadata["counts"]
    counts.update({name: sum(row["state"] == state for row in records)
                   for name, state in (("kept", "keep"), ("uncertain", "uncertain"),
                                       ("pending", "pending"), ("errors", "error"),
                                       ("skipped", "skipped"))})
    counts["cards_generated"] = len(cards)
    return {"metadata": metadata, "posts": records, "cards": cards, "uncertain": uncertain}


def markdown(payload: dict) -> str:
    counts = payload["metadata"]["counts"]
    lines = ["# Saved Telegram news", "",
             "Shadow ML only: no calibrated threshold or automatic rejection. "
             "Model revision and truncation coverage are unavailable.", "",
             f"Fetched {counts['fetched']}; new {counts['new']}; updated {counts['updated']}; "
             f"LLM attempts {counts['llm_attempts']}; cards {counts['cards_generated']}.", ""]
    for index, card in enumerate(payload["cards"], 1):
        lines += [f"## {index}. {card['source_id']}", "", card["permalink"], "",
                  card["summary"], "", f"Reason: {card['reason']}", "",
                  "> " + card["evidence_quote"].replace("\n", "\n> "), ""]
    lines += ["## Uncertain (not confirmed selections)", ""]
    for row in payload["uncertain"]:
        lines += [f"- {row['source_id']}: {row['post']['permalink']}",
                  f"  {row['verdict']['reason']}"]
    lines += ["", "## Pending, errors and skipped", ""]
    for row in payload["posts"]:
        if row["state"] in {"pending", "error", "skipped"}:
            error = row.get("error")
            detail = f" ({error['type']})" if error else ""
            lines.append(f"- {row['source_id']}: {row['state']} — {row['reason']}{detail}")
    return "\n".join(lines) + "\n"


__all__ = ["COMPLETED", "record", "payload", "markdown"]
