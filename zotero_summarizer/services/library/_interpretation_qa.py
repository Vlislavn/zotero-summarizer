"""Experiment Q&A: optional visibility never replaces legacy evidence authority."""
import json

from zotero_summarizer.services.faithbench import ANSWER_PROMPT, answer_with_retry
from zotero_summarizer.services.library._grounding import answer_is_supported_by_quote, quote_is_grounded
from zotero_summarizer.services.library._paper_read_meta import artifact_text
from zotero_summarizer.services.library._prompt_security import untrusted_input
from zotero_summarizer.services.library.interpretation import CAUTION, _Client, qualify


def answer(result, question, client, *, max_chars, condition, trace):
    if condition not in {'A', 'B', 'C'} or type(max_chars) is not int or max_chars <= 0:
        raise ValueError('Invalid question condition or context budget')
    card = qualify(result, strict=False)
    content = result['content']
    legacy = artifact_text(content, max_chars=max_chars, review={'digest': result['digest']})
    supplied, visibility = legacy, 'disabled' if condition != 'C' else card['status']
    if condition == 'C' and card['status'] == 'accepted':
        extra = '\n\nAI interpretation draft (not original evidence):\n' + json.dumps(card['items'], ensure_ascii=False)
        visibility = 'whole_card' if len(legacy) + len(extra) <= max_chars else 'whole_card_over_budget'
        if visibility == 'whole_card':
            supplied += extra
    prompt = ANSWER_PROMPT.format(context=untrusted_input(supplied), question=untrusted_input(question))
    if condition != 'A':
        prompt += CAUTION
    parsed, raw = answer_with_retry(_Client(client, 'A', trace), prompt)
    accepted = parsed['answer'] is None or (
        quote_is_grounded(parsed['quote'], legacy) and quote_is_grounded(parsed['quote'], result['text'])
        and quote_is_grounded(parsed['quote'], supplied)
        and (answer_is_supported_by_quote(parsed['answer'], parsed['quote'])
             or answer_is_supported_by_quote(parsed['answer'], legacy))
        and answer_is_supported_by_quote(parsed['answer'], supplied)
    )
    return {'response': parsed if accepted else {'answer': None, 'abstained': True, 'quote': None},
            'raw': raw, 'grounding': 'accepted' if accepted else 'legacy_authority_rejected',
            'card_visibility': visibility}
