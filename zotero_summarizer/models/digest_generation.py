"""New-response contracts; legacy stored digests keep their optional defaults."""
from copy import deepcopy
from typing import Literal

from pydantic import create_model
from pydantic_core import PydanticUndefined

from zotero_summarizer.models.interpretation import InterpretationDigest
from zotero_summarizer.models.research_feed import FeedDigestResponse
from zotero_summarizer.models.triage import PaperDigest

_GENERATION_FIELDS = tuple(name for name in PaperDigest.model_fields if name != 'basis')

_ASSESSMENT_FIELDS = (
    'read_decision', 'read_why', 'read_parts', 'skip_parts',
    'estimated_read_minutes', 'original_value', 'writing_friction', 'writing_reasons',
)


def _generation_model(base, name):
    fields = {}
    for key in _GENERATION_FIELDS:
        field = deepcopy(base.model_fields[key])
        field.default = PydanticUndefined
        field.default_factory = None
        annotation = field.annotation
        if key == 'read_decision':
            annotation = Literal['read', 'skim', 'skip']
        elif key == 'writing_friction':
            annotation = Literal['low', 'moderate', 'high']
        fields[key] = (annotation, field)
    return create_model(name, __base__=base, __module__=__name__, **fields)


PaperDigestGeneration = _generation_model(PaperDigest, 'PaperDigestGeneration')
FeedDigestGeneration = _generation_model(FeedDigestResponse, 'FeedDigestGeneration')
InterpretationDigestGeneration = _generation_model(InterpretationDigest, 'InterpretationDigestGeneration')
