"""Interpretation uses retained declared-field authority, not runtime subtype metadata."""
from copy import deepcopy
from typing import Any

import pytest

from tests._interpretation_experiment import ScriptedClient
from tests.test_interpretation_experiment_core import run
from zotero_summarizer.models import PaperDigest


class EnrichedDigest(PaperDigest):
    unrelated_metadata: Any = None


class EnrichedClient(ScriptedClient):
    def __init__(self, metadata):
        super().__init__()
        self.metadata = metadata

    def pydantic_prompt(self, *, pydantic_model, **kwargs):
        value = super().pydantic_prompt(pydantic_model=pydantic_model, **kwargs)
        if pydantic_model is PaperDigest:
            return EnrichedDigest.model_validate(value | {'unrelated_metadata': self.metadata})
        return value


@pytest.mark.parametrize('arm', ['A', 'B'])
@pytest.mark.parametrize('metadata', [None, {'nested': [1, 1.0, False]}])
def test_typed_subtype_capture_and_both_renderers_preserve_declared_authority(tmp_path, arm, metadata):
    from zotero_summarizer.services.faithbench._interpretation import _render
    from zotero_summarizer.services.library.interpretation import qualify

    result = run(EnrichedClient(metadata), arm)
    assert result['digest']['unrelated_metadata'] == metadata
    assert 'unrelated_metadata' not in result['verification_audit']['verified_digest']
    assert qualify(result)['status'] == 'disabled'
    before = deepcopy(result)
    directory = tmp_path / 'case'
    directory.mkdir()
    _render(directory, {'review': result})
    assert (directory / 'paper_presentation.html').is_file()
    assert (directory / 'zotero-note.html').is_file()
    assert result == before
    result['digest']['tldr'] = 'A different declared finding'
    with pytest.raises(ValueError, match='Current digest'):
        qualify(result)


@pytest.mark.parametrize('metadata', [None, {'nested': [1, 1.0, False]}])
def test_optional_receipt_does_not_certify_or_depend_on_unrelated_metadata(tmp_path, metadata):
    from zotero_summarizer.services.faithbench._interpretation import _render
    from zotero_summarizer.services.library.interpretation import qualify

    result = run(ScriptedClient())
    receipt = deepcopy(result['interpretation'])
    result['digest']['unrelated_metadata'] = metadata
    assert qualify(result)['status'] == 'accepted'
    directory = tmp_path / 'case'
    directory.mkdir()
    _render(directory, {'review': result})
    assert result['interpretation'] == receipt
    assert result['digest']['unrelated_metadata'] == metadata
