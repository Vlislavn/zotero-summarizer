import json
from collections import Counter

import pytest
from langchain_core.outputs import Generation, LLMResult

from tests._interpretation_cohort import cohort
from tests._interpretation_experiment import ScriptedClient
from zotero_summarizer.integrations.llm_callbacks import CompletionGuard


class ObservedSyntheticProvider(ScriptedClient):
    def pydantic_prompt(self, **kwargs):
        value = super().pydantic_prompt(**kwargs)
        self.emit(json.dumps(value))
        return value

    def prompt(self, prompt):
        value = super().prompt(prompt)
        self.emit(value)
        return value

    def emit(self, text):
        CompletionGuard().on_llm_end(LLMResult(generations=[[Generation(text=text)]],
                                              llm_output={'model_name': 'synthetic-not-real-model',
                                                          'token_usage': {'prompt_tokens': 11, 'completion_tokens': 7}}))


def test_complete_model_free_synthetic_run_freezes_and_reopens_all_assigned_outcomes(tmp_path):
    from zotero_summarizer.services.faithbench._interpretation import execute, read_run
    from zotero_summarizer.services.faithbench._interpretation_inputs import inspect

    settings, path, _, config, profiles = cohort(tmp_path)
    constructed = []

    def build(role, profile):
        assert list((settings.faithbench_dir / 'interpretation').glob('*/inputs.json'))
        constructed.append(profile)
        return ObservedSyntheticProvider()

    root = execute(settings, inspect(settings, path), config, profiles, build)
    frozen, records = read_run(root)
    assert len(records) == len(frozen['evaluation']) * len(frozen['recipe']['labels'])
    counts = Counter(record['assignment']['condition'] for record in records for question in record['questions'])
    assert counts == {arm: len(frozen['evaluation']) * 4 for arm in frozen['recipe']['labels']}
    assert all(record['review_status'] == 'returned' for record in records)
    assert all(len(record['questions']) == 4 for record in records)
    assert all(question['status'] == 'returned' for record in records for question in record['questions'])
    assert all(event['raw'] for record in records for call in record['calls'] for event in call['completions'])
    assert all(call['completions'] for record in records for call in record['calls'])
    for record in records:
        directory = root / record['assignment']['directory']
        assert (directory / 'zotero-note.html').is_file()
        assert json.loads((directory / 'paper_audit.json').read_text())['status'] == 'passed'
    # A resealed file inventory cannot discard a publication required by the assigned plan.
    removed = root / records[0]['assignment']['directory'] / 'zotero-note.html'
    removed.unlink()
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files'].pop(str(removed.relative_to(root)))
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='admitted publication inventory'):
        read_run(root)


def test_admission_identity_work_is_outside_generation_plus_four_answer_timing(tmp_path, monkeypatch):
    from zotero_summarizer.services.faithbench import _interpretation
    from zotero_summarizer.services.faithbench._interpretation_inputs import inspect, prepare

    settings, path, _, config, profiles = cohort(tmp_path)
    _, frozen = prepare(settings, inspect(settings, path), config, profiles)
    clock = [0.0]

    def identity():
        clock[0] += 100.0
        return frozen['code']

    monkeypatch.setattr(_interpretation, 'code_identity', identity)
    monkeypatch.setattr(_interpretation, 'perf_counter', lambda: clock[0])
    assignment = {'paper': frozen['evaluation'][0], 'condition': 'A'}
    record = _interpretation._case(frozen, config, dict.fromkeys(profiles, ObservedSyntheticProvider()), assignment)
    assert record['review_status'] == 'returned'
    assert record['seconds'] == 0.0


def test_generation_failures_retain_four_failed_questions_not_successful_abstentions(tmp_path):
    from zotero_summarizer.services.faithbench._interpretation import execute, read_run
    from zotero_summarizer.services.faithbench._interpretation_inputs import inspect

    class FailedProvider(ScriptedClient):
        def pydantic_prompt(self, **kwargs):
            raise TimeoutError('synthetic generation unavailable')

    settings, path, _, config, profiles = cohort(tmp_path)
    root = execute(settings, inspect(settings, path), config, profiles, lambda role, profile: FailedProvider())
    frozen, records = read_run(root)
    assert len(records) == len(frozen['evaluation']) * 3
    assert all(record['review'] is None and record['review_status'] == 'failed' for record in records)
    assert all(len(record['questions']) == 4 for record in records)
    assert all(row['status'] == 'failed' and 'output' not in row for record in records for row in record['questions'])
    assert all(record['calls'][0]['status'] == 'failed' for record in records)
    assert all(not (root / record['assignment']['directory'] / 'zotero-note.html').exists() for record in records)
