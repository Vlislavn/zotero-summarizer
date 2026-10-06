"""Observed retrieval accounting; merged families count once per contributing source."""
from collections import Counter

from zotero_summarizer.services.search._publication_types import adequate_types


def account_retrieval(raw, families, accepted, plan, requests):
    merged = Counter(source for candidate in families for source in {p.source for p in candidate.provenance})
    kept = Counter(source for candidate in accepted for source in {p.source for p in candidate.provenance})
    kept_ids = {c.candidate_id for c in accepted}
    unknown = Counter(source for c in families if c.candidate_id not in kept_ids
                      and plan.study_types and plan.constraint_origin != 'legacy_unknown'
                      and not adequate_types(c, plan.study_types) for source in {p.source for p in c.provenance})
    sources = dict.fromkeys([*(request['source'] for request in requests), *raw])
    return {source: {'observations': raw[source], 'families': merged[source],
                     'duplicate_observations': raw[source] - merged[source], 'accepted': kept[source],
                     'constraint_rejected': merged[source] - kept[source],
                     'unknown_type_rejected': unknown[source],
                     'disallowed_rejected': merged[source] - kept[source] - unknown[source],
                     'status': 'observed' if raw[source] else 'unknown',
                     'variants': [request for request in requests if request['source'] == source]}
            for source in sources}
