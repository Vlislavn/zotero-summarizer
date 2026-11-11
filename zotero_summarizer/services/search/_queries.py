"""Bounded complementary lexical passes; optional expansions are not eligibility."""
import re


def supplied_domain(intent):
    raw = ' ' + ' '.join(re.findall(r'\w+', intent.raw_query.casefold())) + ' '
    domain = ' '.join(re.findall(r'\w+', intent.domain.casefold()))
    return intent.domain if domain and ' ' + domain + ' ' in raw else ''


def _quote(term):
    return '"' + term.replace('"', ' ').replace('\\', ' ').strip() + '"'


_STOP = frozenset("a an and as at by for from in is of on or the to vs via with are be can do does how what which".split())


def _content_tokens(text):
    return [t for t in re.findall(r'[\w-]+', text.casefold()) if t not in _STOP]


def _group(terms, prefix):
    if not terms:
        return ''
    group = ' OR '.join(prefix + _quote(term) for term in terms)
    return group if len(terms) == 1 else '(' + group + ')'


def complementary_queries(intent, tight):
    """Lexical variants (OpenAlex, Europe PMC, arXiv), tight first.

    The model's ``search_queries`` are the variants, in its order. Without them (parse failure,
    stored intent) the fallback is the quoted raw head plus one OR bag of the concepts, which must
    co-occur with at least one word of the topic (the supplied domain, else any content word of the
    raw topic); with no such word the bag is dropped."""
    anchor = supplied_domain(intent)
    # search_arxiv prefixes the whole query with ``all:``; a leading "(" keeps every arXiv variant a valid field group.
    if intent.search_queries:
        boolean = list(intent.search_queries)
        arxiv = list(boolean)  # plain terms: arXiv ranks by relevance; an AND of 5+ words matches ~1 paper
    else:
        boolean = [tight or _quote(intent.raw_query)]
        arxiv = ['(all:' + (tight or _quote(intent.raw_query)) + ')']
        concepts = intent.concepts[:6] or [intent.raw_query]
        # One broad pass includes every bounded alternative: the two-pass OpenAlex cap
        # must not discard later concepts or acronym-only coverage.
        pool = list(dict.fromkeys([*concepts, *intent.synonyms[:3], *intent.related_terms[:3]]))
        alternatives = [term for term in pool if term != anchor]
        # No generic single concept may retrieve alone: the group is ANDed with a topic anchor.
        anchors = [anchor] if anchor else list(dict.fromkeys(t for t in _content_tokens(intent.raw_query) if len(t) > 2))
        for prefix, out in (('', boolean), ('all:', arxiv)):
            head = _group(anchors, prefix)
            bag = _group(alternatives, prefix)
            if head and bag:
                out.append(head + ' AND ' + bag)
            elif head and anchor:
                out.append(head)
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
