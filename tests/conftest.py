from __future__ import annotations

import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_onprem_stubs() -> None:
    if "onprem.llm" in sys.modules and "onprem.ingest.base" in sys.modules:
        return

    onprem_module = sys.modules.setdefault("onprem", types.ModuleType("onprem"))
    llm_module = types.ModuleType("onprem.llm")
    ingest_module = types.ModuleType("onprem.ingest")
    ingest_base_module = types.ModuleType("onprem.ingest.base")

    class DummyLLM:
        def __init__(self, *args, **kwargs):
            # Capture kwargs so tests can introspect model name etc.
            self.model = kwargs.get("model", args[1] if len(args) > 1 else None)
            self._kwargs = kwargs

        def prompt(self, *args, **kwargs):
            raise NotImplementedError("DummyLLM.prompt should not be called in unit tests")

        def pydantic_prompt(self, *args, **kwargs):
            raise NotImplementedError("DummyLLM.pydantic_prompt should not be called in unit tests")

    def dummy_load_single_document(*args, **kwargs):
        return []

    llm_module.LLM = DummyLLM
    ingest_base_module.load_single_document = dummy_load_single_document
    ingest_module.base = ingest_base_module
    onprem_module.llm = llm_module
    onprem_module.ingest = ingest_module

    sys.modules["onprem.llm"] = llm_module
    sys.modules["onprem.ingest"] = ingest_module
    sys.modules["onprem.ingest.base"] = ingest_base_module


_install_onprem_stubs()
