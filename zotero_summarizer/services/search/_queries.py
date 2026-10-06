"""Bounded complementary lexical passes; optional expansions are not eligibility."""
import re


def supplied_domain(intent):
    raw = ' ' + ' '.join(re.findall(r'\w+', intent.raw_query.casefold())) + ' '
    domain = ' '.join(re.findall(r'\w+', intent.domain.casefold()))
    return intent.domain if domain and ' ' + domain + ' ' in raw else ''


def _quote(term):
    return '"' + term.replace('"', ' ').replace('\\', ' ').strip() + '"'


def complementary_queries(intent, tight):
    concepts = intent.concepts[:6] or [intent.raw_query]
    anchor = supplied_domain(intent)
    # One broad pass includes every bounded alternative: the two-pass OpenAlex cap
    # must not discard later concepts or acronym-only coverage.
    alternatives = list(dict.fromkeys([*concepts, *intent.synonyms[:3], *intent.related_terms[:3]]))
    groups = [alternatives]
    boolean = [tight or _quote(intent.raw_query)]
    arxiv = ['all:' + (tight or _quote(intent.raw_query))]
    for group in groups:
        # Alternatives are grouped under a domain anchor, never OR'ed against the whole topic.
        alternatives = [term for term in group if term != anchor]
        query = '(' + ' OR '.join(_quote(term) for term in alternatives) + ')' if alternatives else ''
        arxiv_query = '(' + ' OR '.join('all:' + _quote(term) for term in alternatives) + ')' if alternatives else ''
        if anchor:
            query = _quote(anchor) + (' AND ' + query if query else '')
            arxiv_query = 'all:' + _quote(anchor) + (' AND ' + arxiv_query if arxiv_query else '')
        if query:
            boolean.append(query)
            arxiv.append(arxiv_query)
    if intent.constraint_origin == 'user_confirmed':
        boolean = [_apply_filters(query, intent, '') for query in boolean]
        arxiv = [_apply_filters(query, intent, 'all:') for query in arxiv]
    return tuple(list(dict.fromkeys(queries)) for queries in (boolean, boolean, arxiv))


def _apply_filters(query, intent, prefix):
    clauses = ['(' + query + ')', *(prefix + _quote(term) for term in intent.must_include)]
    result = ' AND '.join(clauses)
    for term in intent.must_not_include:
        result += ' NOT ' + prefix + _quote(term)
    return result
