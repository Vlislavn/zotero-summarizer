"""Question-only mutation identity, append receipts and alias snapshots."""
from contextlib import closing
from uuid import UUID

from zotero_summarizer.storage import review_notes, review_questions
from zotero_summarizer.storage.repositories import _connect_to


def validate_mutation(request):
    """Parse a create envelope using the immutable question store's validator."""
    if request.get("operation") != "create":
        raise ValueError("review_question operation must be create")
    if request.get("resolves_mutation_id") is not None:
        raise ValueError("question creates cannot resolve manual-field conflicts")
    payload = request.get("value")
    if not isinstance(payload, dict) or set(payload) != set(review_questions._PAYLOAD_FIELDS):
        raise ValueError("review_question value must contain exactly the immutable submission fields")
    values = review_questions._payload_values(payload)
    try:
        mutation_id = str(UUID(request.get("mutation_id")))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("mutation_id must be a UUID") from exc
    if mutation_id != values[0]:
        raise ValueError("mutation_id must equal submission_id")
    if request.get("item_key") != values[1]:
        raise ValueError("item_key must equal the original question item_key")
    device = request.get("device_id")
    if not isinstance(device, str) or not device.strip() or len(device) > 100:
        raise ValueError("device_id must be nonblank text of at most 100 characters")
    return {**request, "mutation_id": mutation_id, "device_id": device.strip(),
            "value": dict(zip(review_questions._PAYLOAD_FIELDS, values))}


def apply(conn, request):
    """Save/reuse a question in the caller's receipt transaction, ignoring bases."""
    row, created = review_questions.save(conn, request["value"])
    revision = conn.execute(
        "SELECT revision FROM sync_changes WHERE field='review_question' AND value=? "
        "ORDER BY revision LIMIT 1", (row["submission_id"],),
    ).fetchone()
    if revision is None:
        raise RuntimeError("saved question has no sync revision")
    return {"mutation_id": request["mutation_id"], "status": "applied",
            "applied_revision": int(revision[0]),
            "previous": {"value": None if created else row}, "canonical": {"value": row}}


def list_for_paper(db_path, item_key):
    with closing(_connect_to(db_path)) as conn:
        return review_questions.list_for_paper(conn, item_key)


def snapshots(conn):
    """Project separate question arrays onto every resolved alias, once per family."""
    seeds = conn.execute("SELECT DISTINCT item_key FROM review_questions ORDER BY item_key").fetchall()
    fields = {}
    for (seed,) in seeds:
        if (seed, "review_question") in fields:
            continue
        keys = review_notes.resolve_item_key_aliases(conn, seed)
        questions = review_questions.list_for_paper(conn, seed)
        placeholders = ",".join("?" for _ in keys)
        revision = conn.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM sync_changes "
            f"WHERE field='review_question' AND item_key IN ({placeholders})", keys,
        ).fetchone()[0]
        for key in keys:
            fields[(key, "review_question")] = {
                "item_key": key, "field": "review_question", "value": questions,
                "revision": int(revision),
            }
    return fields
