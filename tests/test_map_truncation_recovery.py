"""Output truncation subdivides source work without accepting partial notes."""
import pytest

from zotero_summarizer.integrations import llm_callbacks
from zotero_summarizer.services.library import _map_reduce


class Mapper:
    def __init__(self, threshold=1800, error=None):
        self.threshold = threshold
        self.error = error
        self.chunks = []
        self.successful = []

    def prompt(self, prompt):
        chunk = prompt.split('<untrusted_input>')[-1].split('</untrusted_input>')[0]
        self.chunks.append(chunk)
        if self.error:
            raise self.error
        if len(chunk) > self.threshold:
            raise llm_callbacks.LLMOutputTruncated('length')
        self.successful.append(chunk)
        return f'note {len(self.successful)}'


@pytest.mark.parametrize("size", [4001, 6500, 8000])
def test_subdivision_covers_every_source_character(monkeypatch, size):
    monkeypatch.setattr(_map_reduce, 'untrusted_input', lambda text: f'<untrusted_input>{text}</untrusted_input>')
    source = ''.join(chr(0x400 + i) for i in range(size))
    mapper = Mapper()
    result = _map_reduce._map_chunk(mapper, source)
    assert set(source) == set(''.join(mapper.successful))
    assert len(mapper.chunks) < 32
    assert max(map(len, mapper.successful)) <= 1800
    assert 'generation notes' in result
    assert all(f'note {i}' in result for i in range(1, len(mapper.successful) + 1))


def test_short_success_is_unchanged():
    mapper = Mapper(threshold=100000)
    assert _map_reduce._map_chunk(mapper, 'short source') == 'note 1'
    assert len(mapper.chunks) == 1


@pytest.mark.parametrize('size', [999, 1000])
def test_exhausted_floor_propagates(size):
    mapper = Mapper(error=llm_callbacks.LLMOutputTruncated('length'))
    with pytest.raises(llm_callbacks.LLMOutputTruncated):
        _map_reduce._map_chunk(mapper, 'x' * size)
    assert len(mapper.chunks) == 1


@pytest.mark.parametrize('error', [RuntimeError('unknown'), ValueError('invalid'), KeyboardInterrupt()])
def test_other_errors_are_not_retried(error):
    mapper = Mapper(error=error)
    with pytest.raises(type(error)):
        _map_reduce._map_chunk(mapper, 'x' * 5000)
    assert len(mapper.chunks) == 1


def test_empty_output_still_fails():
    class Empty:
        def prompt(self, prompt):
            return ' '
    with pytest.raises(ValueError, match='empty chunk summary'):
        _map_reduce._map_chunk(Empty(), 'x' * 5000)


def test_recursive_floor_exhaustion_is_finite(monkeypatch):
    monkeypatch.setattr(_map_reduce, 'untrusted_input', lambda text: f'<untrusted_input>{text}</untrusted_input>')
    mapper = Mapper(threshold=0)
    with pytest.raises(llm_callbacks.LLMOutputTruncated):
        _map_reduce._map_chunk(mapper, 'x' * 8000)
    assert list(map(len, mapper.chunks)) == [8000, 4000, 2000, 1000]


def test_caller_cancellation_is_checked_before_any_recovery_provider_call(monkeypatch):
    monkeypatch.setattr(_map_reduce, 'untrusted_input', lambda text: f'<untrusted_input>{text}</untrusted_input>')

    class Cancelled(RuntimeError):
        pass

    mapper = Mapper(threshold=0)

    def check():
        if mapper.chunks:
            raise Cancelled('cancelled after first provider call')

    with pytest.raises(Cancelled):
        _map_reduce._map_chunk(mapper, 'x' * 8000, check_cancelled=check)
    assert len(mapper.chunks) == 1
