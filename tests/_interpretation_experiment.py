import re
from copy import deepcopy

from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.setup.bootstrap import _default_goals_config
from zotero_summarizer.services.library._map_reduce import ChunkBudget

TEXT = ("The sample retained alpha. The studied cohort was adults. The design was observational. "
        "External validation was not performed. Treatment recommendations were not established. ") * 20
PAYLOAD = dict(tldr="The sample retained alpha", key_findings=["The sample retained alpha"],
               methods="The design was observational", limitations="External validation was not performed",
               parameters={"external_validation": False}, read_decision="skip", read_why="", read_parts=[],
               skip_parts=[], estimated_read_minutes=None, original_value="", writing_friction="low",
               writing_reasons=[])
CARD = dict(finding_index=0, finding=PAYLOAD['key_findings'][0], basis_refs=['methods', 'parameters'],
            studied_context="The studied cohort was adults", inference_strength="association",
            rationale="The design was observational", uncertainties=["External validation was not performed"],
            unsupported_extrapolations=["Treatment recommendations were not established"])


def inputs():
    config = _default_goals_config()
    config.quality_review.max_text_chars = len(TEXT) + 10
    content = {'title': 'Frozen synthetic source', 'full_text': TEXT}
    return {'title': content['title'], 'text': TEXT, 'content': content}, config, ChunkBudget(len(TEXT) + 10, 8000, 1)


class ScriptedClient:
    def __init__(self, *, cards=True, responses=None, reject_optional=False):
        self.calls = []
        self.cards, self.responses, self.reject_optional = cards, list(responses or []), reject_optional

    def pydantic_prompt(self, *, prompt, pydantic_model, **kwargs):
        self.calls.append((prompt, pydantic_model, deepcopy(kwargs)))
        if issubclass(pydantic_model, PaperDigest):
            value = deepcopy(self.responses.pop(0) if self.responses else PAYLOAD)
            if self.cards and 'interpretation' in pydantic_model.model_fields:
                value['interpretation'] = [deepcopy(CARD)]
            return value
        count = max(map(int, re.findall(r'\[(\d+)\]', prompt)), default=-1) + 1
        indices = range(count)
        return {'checks': [{'index': i, 'supported': not self.reject_optional or 'interpretation[' not in prompt,
                            'evidence': [] if self.reject_optional and 'interpretation[' in prompt else [0]}
                           for i in indices]}

    def prompt(self, prompt):
        self.calls.append((prompt, None, {}))
        return '{"answer":null,"quote":null,"abstained":true}'


def run_review(client, condition='C', *, strategy='rank', paper=None, config=None, budget=None, mapper=None, trace=None):
    from zotero_summarizer.services.library.interpretation import review
    from zotero_summarizer.services.library._interpretation_trace import Trace

    original, default_config, default_budget = inputs()
    if paper is not None and isinstance(paper.get('text'), str):
        default_config.quality_review.max_text_chars = len(paper['text']) + 10
        default_budget = default_budget._replace(max_chars=len(paper['text']) + 10)
    config = config if config is not None else default_config
    config.quality_review.chunk_strategy = strategy
    return review(paper if paper is not None else original, config,
                  (mapper if mapper is not None else client, client),
                  budget if budget is not None else default_budget, condition,
                  trace if trace is not None else Trace(max_calls=100))


def reply(payload, form):
    import json
    from zotero_summarizer.models.interpretation import InterpretationDigest

    if form == 'parsed':
        return InterpretationDigest.model_validate(payload)
    if form == 'dict':
        return deepcopy(payload)
    text = json.dumps(payload)
    if form == 'fenced':
        return '```json\n' + text + '\n```'
    return 'Here is the digest: ' + text + ' End.' if form == 'prose' else text
