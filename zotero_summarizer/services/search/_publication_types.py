"""Recover absent methodology metadata by exact DOI, never by prose."""
import os
import re

from zotero_summarizer.domain import normalize_doi
from zotero_summarizer.integrations.europepmc import search_europepmc
from zotero_summarizer.settings import offline_requested

_DEFAULT_LOOKUPS = 5
_MAX_LOOKUPS = 15


def adequate_types(candidate, requested):
    types = {term.casefold().strip() for term in candidate.publication_types}
    return bool(types & {term.casefold().strip() for term in requested})


def recover_types(candidates, plan):
    if not plan.study_types or plan.constraint_origin == 'legacy_unknown' or offline_requested():
        return {'requests': 0, 'status': 'not_requested'}
    budget = int(os.environ.get('ZS_SEARCH_TYPE_LOOKUPS', str(_DEFAULT_LOOKUPS)))
    if not 0 <= budget <= _MAX_LOOKUPS:
        raise ValueError(f'ZS_SEARCH_TYPE_LOOKUPS must be between 0 and {_MAX_LOOKUPS}')
    unknown = [candidate for candidate in candidates
               if candidate.doi and not adequate_types(candidate, plan.study_types)]
    phrases = [re.findall(r"\w+", term.casefold()) for term in plan.study_types]

    def title_hint(candidate):
        title = " " + " ".join(re.findall(r"\w+", candidate.title.casefold())) + " "
        return any(tokens and (" " + " ".join(tokens) + " ") in title for tokens in phrases)

    requests = confirmed = 0
    for candidate in sorted(unknown, key=lambda candidate: not title_hint(candidate)):
        if requests >= budget:
            break
        requests += 1
        doi = candidate.doi.replace('"', '')
        hits = search_europepmc(f'DOI:"{doi}"', page_size=1)
        for hit in hits:
            if normalize_doi(hit.doi) == normalize_doi(candidate.doi):
                candidate.publication_types = list(dict.fromkeys(candidate.publication_types + hit.publication_types))
        confirmed += int(adequate_types(candidate, plan.study_types))
    return {'requests': requests, 'budget': budget, 'unattempted': len(unknown) - requests,
            'confirmed': confirmed, 'status': 'unknown' if requests else 'not_requested'}
