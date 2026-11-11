"""Invocation-scoped observations, including raw completions before parser reduction."""
from copy import deepcopy
from threading import Lock
from time import perf_counter

from zotero_summarizer.integrations.llm_callbacks import observe_completions


class Trace:
    def __init__(self, *, max_calls):
        if type(max_calls) is not int or max_calls <= 0:
            raise ValueError('max_calls must be a positive integer')
        self.max_calls, self.calls, self.lock = max_calls, [], Lock()

    def invoke(self, client, method, prompt, kwargs):
        model = kwargs.get('pydantic_model')
        row = {'method': method, 'prompt': prompt, 'completions': [], 'status': 'started',
               'schema': model.model_json_schema() if model is not None else None,
               'kwargs': deepcopy({key: value for key, value in kwargs.items() if key != 'pydantic_model'})}
        with self.lock:
            if len(self.calls) >= self.max_calls:
                raise ValueError('experiment client-call budget exhausted')
            self.calls.append(row)

        def observe(response):
            output = response.llm_output or {}
            event = {'usage': deepcopy(output.get('token_usage')), 'model': output.get('model_name'),
                     'raw': [[{'text': g.text, 'finish_reason': (g.generation_info or {}).get('finish_reason')}
                              for g in group] for group in response.generations]}
            with self.lock:
                row['completions'].append(event)

        start = perf_counter()
        try:
            with observe_completions(observe):
                value = getattr(client, method)(prompt=prompt, **kwargs)
            row['decoded'] = value.model_dump(mode='json', exclude_unset=True, warnings=False) if hasattr(value, 'model_dump') else deepcopy(value)
            row['status'] = 'returned'
            return value
        except Exception as exc:
            row.update(status='failed', failure_type=type(exc).__name__)
            raise
        finally:
            row['seconds'] = perf_counter() - start
