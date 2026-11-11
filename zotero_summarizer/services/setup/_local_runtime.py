"""Task-scoped, explicitly requested local preparation; never called by reads/save."""
from contextlib import contextmanager
from pathlib import Path

from zotero_summarizer.integrations.ollama_runtime import OwnedOllamaRuntime
from zotero_summarizer.services.setup.load_attempt import current_attempt


@contextmanager
def _owned_local_runtime(settings, model, directory, guard_receipt, *, source_url='http://127.0.0.1:11434',
                         kv_cache_type='f16', context_tokens=32768, batch_size=128, thinking_effort='off', max_tokens=4096,
                         rss_limit_mib=4096):
    attempt = current_attempt()
    if attempt is None:
        raise RuntimeError('Local preparation must belong to an explicit SetupAttempt')
    for path in (directory, guard_receipt):
        if not Path(path).resolve().is_relative_to(settings.data_dir.resolve()):
            raise ValueError('Owned runtime state must remain below the selected Settings.data_dir')
    runtime = OwnedOllamaRuntime(source_url, model, directory, guard_receipt,
                                kv_cache_type=kv_cache_type, context_tokens=context_tokens,
                                batch_size=batch_size, thinking_effort=thinking_effort, max_tokens=max_tokens,
                                rss_limit_mib=rss_limit_mib)
    with runtime, attempt.own_runtime(runtime):
        yield runtime
