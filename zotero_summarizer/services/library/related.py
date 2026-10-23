"""Related saved papers over existing library search; no reviews or native writes."""
from difflib import SequenceMatcher
import re
import sqlite3
from urllib.parse import urlsplit

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.zotero_read import ZoteroReadError, is_expected_sqlite_io
from zotero_summarizer.models.paper_keys import validate_paper_item_key
from zotero_summarizer.services._common import settings, state
from zotero_summarizer.services.library._digest_audit import canonical_hash
from zotero_summarizer.services.library._related_context import collect_comments
from zotero_summarizer.services.library._search import hybrid_search
from zotero_summarizer.services.library.app_library_reader import AppLibraryReader
from zotero_summarizer.services.search._models import Candidate
from zotero_summarizer.services.zotero.zotero import get_library_reader, resolve_reader_for_key
from zotero_summarizer.storage.library_context import read_context


def public_query(detail):
    """Allowlist only public bibliographic fields; never serialize the detail object."""
    return '\n'.join(str(detail.get(field) or '') for field in ('title', 'abstract')).strip()[:4000]


def identifiers(detail):
    url = str(detail.get('url') or '')
    parsed = urlsplit(url)
    arxiv = detail.get('arxiv_id') or (url if parsed.hostname == 'arxiv.org'
                                      and parsed.path.startswith(('/abs/', '/pdf/')) else '')
    doi = detail.get('doi') or (parsed.path.lstrip('/') if parsed.hostname == 'doi.org' else '')
    return set(Candidate(title=detail.get('title', ''), doi=doi, arxiv_id=arxiv).identifier_keys())


def _detail(reader, key):
    try:
        detail = reader.get_item_detail(key)
    except ZoteroReadError as exc:
        if not exc.expected_io:
            raise
        return None
    except sqlite3.Error as exc:
        if not is_expected_sqlite_io(exc):
            raise
        return None
    except OSError:
        return None
    if detail is not None:
        detail = {**detail, 'native_comments_available': not isinstance(reader, AppLibraryReader)}
    return detail


def _snapshot(item_key):
    validate_paper_item_key(item_key)
    reader = get_library_reader()
    seed = resolve_reader_for_key(item_key).get_item_detail(item_key)
    if seed is None:
        raise APIError(error='not_found', message='Paper not found', status_code=404)
    items = {row['item_key']: row for row in reader.get_all_items(include_abstract=True)['items']}
    try:
        contexts = read_context(settings().triage_db_path, list(dict.fromkeys([item_key, *items])))
    except sqlite3.Error as exc:
        if not is_expected_sqlite_io(exc):
            raise
        contexts = {}
    except OSError:
        contexts = {}
    aliases = set(contexts.get(item_key, {}).get('aliases', [item_key]))
    seed_ids = identifiers(seed)
    saved = bool(aliases.intersection(items)) or any(seed_ids & identifiers(row) for row in items.values())
    return reader, seed, items, contexts, aliases, saved


def _overlap(seed, candidate):
    matches = []
    for left in ('title', 'abstract'):
        for right in ('title', 'abstract'):
            a, b = str(seed.get(left) or ''), str(candidate.get(right) or '')
            aw, bw = list(re.finditer(r'\S+', a)), list(re.finditer(r'\S+', b))
            match = SequenceMatcher(None, [x.group().casefold() for x in aw],
                                    [x.group().casefold() for x in bw], autojunk=False).find_longest_match()
            if match.size < 2:
                continue
            start, end = aw[match.a].start(), aw[match.a + match.size - 1].end()
            if end - start < 12:
                continue
            other_start, other_end = bw[match.b].start(), bw[match.b + match.size - 1].end()
            matches.append((end - start, {
                'seed': {'item_key': seed['item_key'], 'field': left, 'start': start, 'end': end, 'excerpt': a[start:end]},
                'candidate': {'item_key': candidate['item_key'], 'field': right, 'start': other_start,
                              'end': other_end, 'excerpt': b[other_start:other_end]},
            }))
    if not matches:
        return {'kind': 'similarity_only', 'sentence': 'Library-search similarity; a concrete relationship is unverified.'}, {}
    evidence = max(matches, key=lambda pair: pair[0])[1]
    phrase = evidence['seed']['excerpt']
    return {'kind': 'metadata_overlap', 'sentence': f'Both metadata records mention “{phrase}”; this is not confirmation or contradiction.'}, evidence


def _result(seed, candidate, detail, context, score):
    relationship, evidence = _overlap(seed, candidate)
    return {'item_key': candidate['item_key'], 'title': candidate['title'], 'score': score,
            'relationship': relationship, 'evidence': evidence,
            'source_basis': 'abstract_only' if seed.get('abstract') and candidate.get('abstract') else 'title_only',
            'comments': collect_comments(candidate['item_key'], detail, context)}


def related_papers(item_key):
    reader, seed, items, contexts, aliases, saved = _snapshot(item_key)
    seed = {**seed, 'item_key': item_key}
    seed_ids = identifiers(seed)
    candidates = {key: row for key, row in items.items()
                  if key not in aliases and not seed_ids.intersection(identifiers(row))}
    base = {'seed_key': item_key, 'saved_seed': saved,
            'seed_comments': collect_comments(item_key, _detail(resolve_reader_for_key(item_key), item_key), contexts.get(item_key)),
            'ranking_basis': 'existing_hybrid_public_metadata', 'comments_in_ranking': False,
            'comment_ranking_qualification': 'unavailable_semantic_intent_unqualified'}
    if not candidates:
        return {**base, 'status': 'empty', 'items': []}
    ordered, scores, status = hybrid_search(public_query(seed), list(candidates), top_n=50)
    results, seen_ids, seen_aliases = [], set(seed_ids), set(aliases)
    for key in ordered:
        detail = _detail(reader, key)
        row = {**candidates[key], **{field: (detail or {}).get(field, candidates[key].get(field, ''))
                                   for field in ('doi', 'arxiv_id', 'url')}}
        family = set(contexts.get(key, {}).get('aliases', [key]))
        ids = identifiers(row)
        if family & seen_aliases or ids & seen_ids:
            continue
        seen_ids.update(ids)
        seen_aliases.update(family)
        results.append(_result(seed, row, detail, contexts.get(key), scores.get(key)))
        if len(results) == 3:
            break
    availability = 'unavailable' if status.get('semantic_unavailable') else ('ready' if results else 'empty')
    return {**base, 'status': availability, 'search_status': status, 'items': results[:3]}


def _retrieval_identity():
    app = state()
    config = app.app_state.config
    cache = getattr(app, 'embedding_cache', None)
    return {'config': canonical_hash(config.model_dump(mode='json')),
            'encoder': getattr(cache, '_encoder_id', None), 'contract': 'related-public-v1'}


def discovery_context(item_key):
    """Explicit-click snapshot includes full note bodies, not counts or clocks alone."""
    reader, seed, items, contexts, aliases, saved = _snapshot(item_key)
    if not saved:
        raise APIError(error='saved_seed_required', message='Save this paper before discovery', status_code=409)
    notes = {}
    for key, row in items.items():
        detail = _detail(reader, key)
        notes[key] = collect_comments(key, detail, contexts.get(key))
        items[key] = {**row, **{field: (detail or {}).get(field, row.get(field, ''))
                               for field in ('doi', 'arxiv_id', 'url')}}
    fingerprint = canonical_hash({'seed_query': public_query(seed), 'seed_identifiers': sorted(identifiers(seed)),
                                  'aliases': sorted(aliases), 'items': items, 'notes': notes,
                                  'contexts': {key: contexts.get(key) for key in items},
                                  'identity': _retrieval_identity()})
    return {'seed_key': item_key, 'seed': seed, 'items': items, 'aliases': aliases,
            'fingerprint': fingerprint, 'query': public_query(seed)}
