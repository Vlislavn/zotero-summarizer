"""Explicit saved-seed discovery reuses Search screening, never automatic review/Add."""
from zotero_summarizer.services.library._digest_audit import canonical_hash
from zotero_summarizer.services.library.related import discovery_context, identifiers
from zotero_summarizer.services.search import require_online, session as session_store
from zotero_summarizer.services.search.pipeline import default_deps, run_screen


def _exclude_saved(sess, context):
    known_ids = set().union(*(identifiers(row) for row in context['items'].values()))
    known_ids.update(identifiers(context['seed']))
    known_keys = set(context['items']) | context['aliases']
    kept = []
    excluded = 0
    for candidate in sess.candidates:
        if (candidate.in_library is True or set(candidate.identifier_keys()) & known_ids
                or candidate.existing_zotero_key in known_keys or candidate.materialized_zotero_key in known_keys):
            excluded += 1
        else:
            kept.append(candidate)
    sess.candidates = kept
    return excluded


def _source_receipts(sess):
    return [{**variant['outcome'], 'variant': index}
            for counts in sess.plan.retrieval_accounting.values() if isinstance(counts, dict)
            for index, variant in enumerate(counts.get('variants', [])) if 'outcome' in variant]


def _availability(receipts):
    states = {receipt['status'] for receipt in receipts}
    if not states or 'unknown' in states:
        return 'partial_failure' if 'failed' in states else 'unknown'
    if 'failed' in states:
        return 'partial_failure' if states & {'success_empty', 'success_nonempty'} else 'failed'
    return 'not_attempted' if states == {'not_attempted'} else 'observed'


def find_new(item_key, *, refresh=False):
    """Single-flight per seed family; explicit retry may replace a failed snapshot."""
    require_online()
    context = discovery_context(item_key)
    lock_key = 'discovery-' + canonical_hash(sorted(context['aliases']))
    with session_store._lock_for(lock_key):
        previous_id = None
        for summary in session_store.list_sessions():
            sess = session_store.load_unless_deleted(summary['id'])
            if sess is None:
                continue
            receipt = sess.discovery
            if (not receipt.get('superseded_by') and receipt.get('seed_key') in context['aliases']
                    and receipt.get('context_fingerprint') == context['fingerprint'] and sess.status == 'screened'):
                if not refresh:
                    return sess.to_dict()
                previous_id = sess.id
        # Only the allowlisted public title/abstract enters the existing planner,
        # source adapters and reranker. No private comment is passed to any provider.
        deps = default_deps()
        deps.observe_source_outcomes = True
        sess = run_screen(context['query'], [], deps=deps)
        excluded = _exclude_saved(sess, context)
        receipts = _source_receipts(sess)
        availability = _availability(receipts)
        sess.discovery = {'seed_key': item_key, 'context_fingerprint': context['fingerprint'],
                          'mode': 'discovery_only', 'excluded_saved': excluded,
                          'comments_used': False, 'provider_availability': availability, 'source_outcomes': receipts,
                          'membership_unknown': sum(candidate.in_library is None for candidate in sess.candidates)}
        session_store.save_merge(sess)
        if previous_id is not None:
            session_store.update(previous_id, lambda old: old.discovery.update(superseded_by=sess.id), skip_deleted=True)
        return sess.to_dict()
