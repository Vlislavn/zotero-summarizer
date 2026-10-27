"""Selected-source conversation collection and immutable v2 publication."""
from __future__ import annotations

from contextlib import closing
import uuid

from zotero_summarizer.models.research_feed import TelegramTarget, TelegramTransportError
from zotero_summarizer.models.telegram_discussion import TelegramConversation, TelegramCollectionStopped, source_records
from zotero_summarizer.services._common import now_iso_z
from zotero_summarizer.services.research_feed import render, telegram_collect, telegram_review
from zotero_summarizer.services.research_feed.telegram import _output_dir
from zotero_summarizer.storage import repositories, research_feed_conversations as store


def targets(profile):
    # Retain existing channel selections until explicitly saved through Sources.
    values = profile.telegram_targets or [TelegramTarget(kind="broadcast", username=name)
                                          for name in profile.telegram_channels]
    if len(values) > profile.telegram_limits.sources or len({target.key for target in values}) != len(values):
        raise ValueError("Telegram source budget exceeded or duplicate source selection")
    return values


async def _collect_target(leaf, target, limits, conn, account, now):
    try:
        return await leaf.collect(target, limits), None
    except (TelegramTransportError, ValueError, PermissionError) as error:
        partial = isinstance(error, TelegramCollectionStopped)
        failure = error.failure if partial else error
        if isinstance(failure, TelegramTransportError):
            stop = telegram_collect._record_error(conn, failure, {}, account, now)
            reason = failure.code
        else:
            stop = True
            reason = "deferred" if isinstance(failure, PermissionError) else "invalid_source_response"
        values = error.observations if partial else [TelegramConversation(
            target=target, status="unavailable", reason=reason, attempted=True, observed_at=now_iso_z())]
        return values, reason if stop or partial else None


async def collect(settings, sdk, profile, *, now):
    from zotero_summarizer.integrations.telegram_threads import TelethonConversationClient

    selected = targets(profile)
    from zotero_summarizer.integrations.telegram_session import _transport_error

    try:
        authorized = await sdk.is_user_authorized()
        failure = None if authorized else "session_unauthorized"
    except Exception as error:
        mapped = _transport_error(error)
        if mapped is None:
            raise
        failure = mapped.code
    if failure is not None:
        return {"authorized": False, "account": {"status": failure, "retry_at": None},
                "conversations": [], "counts": {"sources": 0, "conversations": 0, "messages": 0}}
    account = {"status": "not_checked", "retry_at": None}
    batches = []
    with closing(repositories._connect_to(settings.triage_db_path)) as conn:
        def admit():
            return telegram_collect._admit_request(conn, now, account)

        leaf = TelethonConversationClient(sdk, before_request=admit)
        stopped = None
        for target in selected:
            if stopped:
                values = [TelegramConversation(target=target, status="unavailable",
                    reason=stopped, attempted=False, observed_at=now_iso_z())]
            else:
                values, stopped = await _collect_target(leaf, target, profile.telegram_limits, conn, account, now)
            batches.extend(values)
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                for value in values:
                    store.save_snapshot(conn, value)
    authorized = account["status"] not in {"session_unauthorized", "revoked_session", "missing_session"}
    return {"authorized": authorized, "account": account,
            "conversations": [batch.model_dump(mode="json") for batch in batches],
            "counts": {"sources": len(selected), "conversations": len(batches),
                       "messages": len(source_records(batches))}}


def publish(settings, conversations, *, collection, profile, config=None):
    output = _output_dir(settings)
    topics, identity, errors = [], None, []
    if config is not None and telegram_review.admitted_sources(conversations):
        try:
            result, identity = telegram_review.generate(config, profile, conversations)
            topics = result.model_dump(mode="json")["topics"]
        except Exception as error:
            # Never persist prompt/provider errors or raw SDK exception strings.
            if isinstance(error, telegram_review.TelegramReviewFailure):
                identity = error.identity
            error_type = error.error_type if isinstance(error, telegram_review.TelegramReviewFailure) else type(error).__name__
            errors.append({"stage": "review", "code": "review_failed", "type": error_type})
    partial = any(batch.status in {"unavailable", "truncated"}
                  or (batch.post_window is not None and batch.post_window.truncated) for batch in conversations)
    metadata = {"schema_version": 2, "source": "telegram", "generated_at": now_iso_z(),
        "mode": "collect" if config is None else "review", "collection": collection,
        "scope": "Bounded newest-post/reply snapshots; not complete history, consensus, or paper verification.",
        "limits": profile.telegram_limits.model_dump(), "evaluation_identity": identity,
        "status": "error" if errors else "partial" if partial else "collected" if config is None else "reviewed",
        "counts": {"conversations": len(conversations), "messages": len(source_records(conversations)),
                   "cards_generated": len(topics), "errors": len(errors)}}
    payload = {"metadata": metadata, "conversations": [batch.model_dump(mode="json") for batch in conversations],
               "topics": topics, "errors": errors}
    slug = "telegram-review-" + uuid.uuid4().hex
    originals = [batch.model_dump(mode="json") for batch in conversations]
    path, md = render.persist(payload, output, slug, render_markdown=markdown, inputs=originals)
    return {"json_path": str(path), "markdown_path": str(md), "inputs_path": str(path.parent / "inputs.jsonl"),
            "run_id": slug, "status": metadata["status"], **metadata["counts"]}


def markdown(payload):
    lines = ["# Telegram topic mini-reviews", "", payload["metadata"]["scope"], "",
             "AI-generated, scientifically unverified. Saved historical snapshot.", ""]
    batches = [TelegramConversation.model_validate(value) for value in payload["conversations"]]
    sources = source_records(batches)
    for topic in payload["topics"]:
        lines += ["## " + topic["title"], ""]
        for category in ("context", "news", "discussion", "contrasts", "evidence", "caveats", "implications"):
            assertions = [topic[category]] if category == "context" else topic[category]
            lines += ["### " + category.capitalize(), ""]
            if not assertions:
                lines += ["Not observed/supported in this bounded snapshot.", ""]
            for assertion in assertions:
                lines += [assertion["text"], ""]
                for cite in assertion["citations"]:
                    original = sources[cite["source_id"]]["original"]
                    lines += [f"- {cite['source_id']}: {original.get('permalink') or 'No public permalink'}",
                              "> " + cite["quote"].replace("\n", "\n> ")]
                lines.append("")
    lines += ["## Coverage", ""]
    for batch in batches:
        if batch.post_window is not None:
            window = batch.post_window
            lines += [f"- Post window {batch.target.key}: scanned {window.scanned}; retained {window.retained}; "
                      f"lookahead {window.lookahead}; truncated {window.truncated}"]
        lines += [f"- {batch.target.key}:  {batch.status}; {len(batch.messages)} retained replies/messages; "
                  f"retained texts {batch.text_count}, scanned {batch.scanned_count}, nontext/skipped {batch.skipped_count}, "
                  f"reported total {batch.reported_reply_count}; attempted {batch.attempted}; {batch.reason or 'bounded snapshot'}"]
    lines += ["", "## Errors", ""]
    lines += [f"- {error['stage']}: {error['code']} ({error['type']})" for error in payload["errors"]]
    return "\n".join(lines) + "\n"
