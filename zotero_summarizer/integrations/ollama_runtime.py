"""Explicit, finite-guarded native runtime using installed weights only.

The existing daemon/store are never changed. Residency comes from the owned
listener and runner, not from model inventory or requested context options.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import time
from uuid import uuid4

import httpx
import psutil

from zotero_summarizer.integrations._ollama_store import _clone_installed
from zotero_summarizer.integrations._process_guard import _OWNER, _limits, _same_process
from zotero_summarizer.models.providers import ProviderConfig

_CACHE_TYPES = ('f16', 'q8_0', 'q5_0', 'q4_0')


class OwnedOllamaRuntime:
    def __init__(self, source_url, model, directory, guard_receipt, *, context_tokens=32768,
                 kv_cache_type='f16', batch_size=128, thinking_effort='off', max_tokens=4096,
                 rss_limit_mib=4096):
        self._limits = _limits(rss_limit_mib)
        if type(context_tokens) is not int or context_tokens < 512:
            raise ValueError('A finite positive native execution context is required')
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValueError('A finite positive integer output budget is required')
        if kv_cache_type not in _CACHE_TYPES:
            raise ValueError('Unsupported native calibration KV cache type')
        if type(batch_size) is not int or not 1 <= batch_size <= 1024:
            raise ValueError('Native batch size must be an integer in [1, 1024]')
        self.kv_cache_type = kv_cache_type
        self.batch_size = batch_size
        self.source_url, self.model = source_url, model
        self.directory, self.guard_receipt = Path(directory), Path(guard_receipt)
        self.context_tokens = context_tokens
        self.process = None
        self.root = None
        self.installed = None
        self._output = None
        self.cleanup = {'owned_processes_absent': False}
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        self.origin = f'http://127.0.0.1:{port}'
        self.provider = ProviderConfig(name='local-evaluation', base_url=self.origin + '/v1',
            api_key_env='LOCAL_EVALUATION', auth_mode='none', num_ctx=context_tokens,
            max_tokens=max_tokens, extra_body={'max_tokens': max_tokens}, temperature=0,
            thinking_effort=thinking_effort, max_sub_concurrency=1, structured_output=True)
        self._identity = {'provider': self.provider.model_dump(mode='json'), 'model': model}

    def _guard(self):
        record = json.loads(self.guard_receipt.read_text())
        limits = record['limits']
        if (record.get('owner') != _OWNER or record.get('state') != 'running'
                or record.get('mode') != 'finite' or record.get('guard_stop') is not None
                or limits['min_free_percent'] < 20 or limits['rss_mib'] > self._limits['rss_mib']
                or limits['swap_growth_mib'] > 2048 or not 1 <= limits['wall_seconds'] <= 3600
                or time.time() - self.guard_receipt.stat().st_mtime > 5):
            raise RuntimeError('A fresh finite guardian with unchanged resource limits is required')
        for key in ('guardian', 'sentinel', 'child'):
            if _same_process(record[key]) is None:
                raise RuntimeError('The owned guardian, sentinel or evaluator no longer exists')
        if record['child']['pid'] != os.getpid() or os.getpgid(os.getpid()) != os.getpid():
            raise RuntimeError('This evaluator is not the guardian-owned process group leader')
        memory = psutil.virtual_memory()
        if 100 * memory.available / memory.total < 20:
            raise RuntimeError('Available memory fell below the finite 20% floor')
        return record

    def __enter__(self):
        self._guard()
        self.directory.mkdir(parents=True, exist_ok=False)
        self.installed = _clone_installed(self.source_url, self.model, self.directory / 'models',
                                          batch_size=self.batch_size)
        binary = shutil.which('ollama')
        if binary is None:
            raise RuntimeError('The installed native runtime executable is unavailable')
        home, temporary = self.directory / 'home', self.directory / 'tmp'
        home.mkdir()
        temporary.mkdir()
        environment = {'PATH': os.environ.get('PATH', ''), 'HOME': str(home), 'TMPDIR': str(temporary),
            'OLLAMA_HOST': self.origin, 'OLLAMA_MODELS': str(self.directory / 'models'),
            'OLLAMA_CONTEXT_LENGTH': str(self.context_tokens), 'OLLAMA_NUM_PARALLEL': '1',
            'OLLAMA_MAX_LOADED_MODELS': '1', 'OLLAMA_NO_CLOUD': '1', 'OLLAMA_NOPRUNE': '1',
            'LLAMA_ARG_CACHE_RAM': '0', 'LLAMA_ARG_CTX_CHECKPOINTS': '0',
            'OLLAMA_KV_CACHE_TYPE': self.kv_cache_type,
            'OLLAMA_FLASH_ATTENTION': '1' if self.kv_cache_type != 'f16' else '0'}
        self._output = (self.directory / 'ollama.log').open('xb')
        try:
            # Inherit the evaluator's owned group so guardian/sentinel reap all work.
            self.process = subprocess.Popen([binary, 'serve'], env=environment,
                stdout=self._output, stderr=subprocess.STDOUT)
            self.root = {'pid': self.process.pid, 'created': psutil.Process(self.process.pid).create_time()}
            self._wait_ready()
        except BaseException:
            self._stop()
            raise
        return self

    def _listener_owned(self):
        if self.root is None or _same_process(self.root) is None:
            return False
        port = httpx.URL(self.origin).port
        result = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-p', str(self.root['pid']),
            f'-iTCP:{port}', '-sTCP:LISTEN', '-Fpn'], capture_output=True, text=True, timeout=3)
        return (result.returncode == 0 and f'p{self.root["pid"]}\n' in result.stdout
                and f'n127.0.0.1:{port}\n' in result.stdout)

    def _json(self, path):
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=3) as client:
            response = client.get(self.origin + path)
            response.raise_for_status()
            return response.json()

    def _wait_ready(self):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            self._guard()
            if self.process.poll() is not None:
                raise RuntimeError('The owned native daemon exited during startup')
            if self._listener_owned():
                if self._json('/api/ps')['models']:
                    raise RuntimeError('A fresh isolated daemon unexpectedly has resident models')
                return
            time.sleep(0.1)
        raise TimeoutError('The owned native listener did not become ready')

    def _native_operation(self, operation, owner):
        self._guard()
        if not self._listener_owned():
            raise RuntimeError('The native listener no longer belongs to this runtime')
        request = owner()
        native_id = uuid4().hex
        request.native_started(native_id)
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=120,
                          event_hooks={'request': [request.transport_started]}) as client:
            response = client.post(self.origin + '/api/generate', json={
                'model': self.model, 'prompt': '', 'stream': False,
                'keep_alive': 600 if operation == 'load' else 0,
                'options': {'num_ctx': self.context_tokens, 'num_predict': 0}})
            response.raise_for_status()
            body = response.json()
        if body.get('done') is not True or body.get('response') not in ('', None):
            raise ValueError('Native preparation did not return an empty load/unload completion')
        request.terminal(body.get('model'), body.get('done_reason'), native_id=native_id, native_required=True)
        return body

    def load(self, request_owner):
        """Explicit load-only preparation, never an inference or admission stamp."""
        result = self._native_operation('load', request_owner)
        if len(self._json('/api/ps')['models']) != 1:
            raise ValueError('Load completion did not leave exactly one resident model')
        return result

    def unload(self, request_owner):
        result = self._native_operation('unload', request_owner)
        if self._json('/api/ps')['models']:
            raise ValueError('Native unload did not establish resident-model absence')
        return result

    def observe(self, provider, model):
        self._guard()
        if ({'provider': provider.model_dump(mode='json'), 'model': model} != self._identity
                or not self._listener_owned()):
            raise ValueError('Runtime or provider identity changed')
        rows = self._json('/api/ps')['models']
        if not rows:
            return {'effective_envelope': None, 'available_bytes': None}
        if len(rows) != 1:
            raise ValueError('The isolated runtime has a foreign resident model')
        row = rows[0]
        if row.get('model') != self.model or row.get('digest') != self.installed['digest']:
            raise ValueError('The native resident model/digest differs from the prepared snapshot')
        context, size, vram = row.get('context_length'), row.get('size'), row.get('size_vram')
        if (type(context) is not int or context <= 0 or type(size) is not int or size <= 0
                or type(vram) is not int or not 0 <= vram <= size):
            raise ValueError('Native residency does not establish effective context/placement')
        root = _same_process(self.root)
        runners = [p for p in root.children(recursive=True) if self._is_model_process(p.cmdline())]
        if len(runners) != 1:
            raise ValueError('Exactly one live owned native runner is required')
        batch = self._observed_batch(runners[0].cmdline())
        if batch != self.batch_size:
            raise ValueError('Native model process did not establish the selected execution batch')
        cache = self._observed_cache(runners[0].cmdline())
        if cache != self.kv_cache_type:
            raise ValueError('Native model process did not establish the selected KV cache type')
        log = (self.directory / 'ollama.log').read_text()
        if 'prompt cache is disabled' not in log:
            raise ValueError('Bounded native runtime did not establish disabled prompt-state caching')
        if 'context checkpoints disabled' not in log:
            raise ValueError('Bounded native runtime did not establish disabled context checkpoints')
        placement, domain = self._memory_domain(vram)
        return {'identity': self._identity, 'effective_envelope': {
            'context_tokens': context, 'placement': placement, 'memory_domain': domain},
            'available_bytes': psutil.virtual_memory().available,
            'native': {'model_digest': row['digest'], 'size_bytes': size, 'device_bytes': vram,
                       'runner_pid': runners[0].pid, 'runner_created': runners[0].create_time(),
                       'batch_size': batch, 'kv_cache_type': cache, 'prompt_state_cache_mib': 0,
                       'context_checkpoints': 0}}

    def _is_model_process(self, arguments):
        if 'runner' in arguments:
            return True
        if arguments and Path(arguments[0]).name == 'llama-server' and '--model' in arguments:
            index = arguments.index('--model') + 1
            return index < len(arguments) and arguments[index] == self.installed['model_path']
        return False

    @staticmethod
    def _paired_option(arguments, flags):
        observed = []
        for flag in flags:
            if arguments.count(flag) != 1:
                return None
            index = arguments.index(flag) + 1
            if index >= len(arguments):
                return None
            observed.append(arguments[index])
        return observed[0] if observed[0] == observed[1] else None

    @staticmethod
    def _observed_batch(arguments):
        value = OwnedOllamaRuntime._paired_option(arguments, ('-b', '-ub'))
        return int(value) if value is not None and value.isdigit() else None

    def _observed_cache(self, arguments):
        flags = ('--cache-type-k', '--cache-type-v')
        value = self._paired_option(arguments, flags)
        declared = any(flag in arguments for flag in flags)
        if declared and value not in _CACHE_TYPES:
            return None
        # Allocated K/V types must agree with flags; unsupported native fallback refuses.
        log = (self.directory / 'ollama.log').read_text()
        formats = '|'.join(_CACHE_TYPES)
        kinds = [set(re.findall(rf'\b{label}\s*\(({formats})\)', log)) for label in ('K', 'V')]
        actual = next(iter(kinds[0])) if len(kinds[0]) == 1 and kinds[0] == kinds[1] else None
        return actual if not declared or actual == value else None

    def _memory_domain(self, device_bytes):
        if device_bytes == 0:
            return 'cpu', 'host'
        log = (self.directory / 'ollama.log').read_text()
        if (platform.system() == 'Darwin' and platform.machine() == 'arm64'
                and ('library=Metal' in log or 'library=metal' in log)):
            return 'metal', 'apple-unified'
        raise ValueError('Native device usage has no established matching memory domain')

    def _stop(self):
        if self.root is not None:
            root = _same_process(self.root)
            members = [root, *root.children(recursive=True)] if root else []
            records = [{'pid': p.pid, 'created': p.create_time()} for p in members]
            for process in members:
                try:
                    process.terminate()
                except psutil.NoSuchProcess:
                    continue
            _, alive = psutil.wait_procs(members, timeout=2)
            for process in alive:
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    continue
            psutil.wait_procs(alive, timeout=2)
            self.process.wait(timeout=5)
            self.cleanup = {'owned_processes_absent': all(_same_process(row) is None for row in records),
                            'processes': records}
        if self._output is not None:
            self._output.close()

    def __exit__(self, _type, _value, _traceback):
        self._stop()
