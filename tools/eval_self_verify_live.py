"""Live eval of the self-verification 2nd pass against the LOCAL LLM (run by hand;
NOT a unit test — tests/conftest.py stubs `onprem` offline, so this lives in tools/
like bench_deep_review.py).

Proves the second pass discriminates a real OVER-CLAIM (internal cross-validation
mislabeled as EXTERNAL validation → must be demoted) from a LEGITIMATE external
cohort (must be confirmed) — the positivity-bias guard the feature exists for.

Run:  OPENAI_API_KEY=ollama uv run python tools/eval_self_verify_live.py
Env:  ZS_LIVE_MODEL (default qwen3.5:4b), ZS_LIVE_BASE_URL (default ollama :11434).
Exits non-zero if the live model gets either case wrong.
"""
from __future__ import annotations

import os
import sys

from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.services.library import quality_eval as qe
from zotero_summarizer.services.library._paper_type_checklists import PaperType, spec_for
from zotero_summarizer.services.llm.factory import build_client_for_provider

_BASE = os.environ.get("ZS_LIVE_BASE_URL", "http://localhost:11434/v1")
_MODEL = os.environ.get("ZS_LIVE_MODEL", "qwen3.5:4b")

_OVERCLAIM = ("We performed 5-fold cross-validation on our single-center training cohort "
              "and report the mean AUC.")
_LEGIT = ("The model was externally validated on an independent cohort of 800 patients "
          "from three other hospitals, with AUC 0.81.")


def _verify(llm, quote: str) -> set[str]:
    return qe._self_verify(
        llm, spec=spec_for(PaperType.CLINICAL_PREDICTION),
        rubric={"external_validation": "yes"}, evidence={"external_validation": quote},
        grounded_yes={"external_validation"},
    )


def main() -> int:
    os.environ.setdefault("OPENAI_API_KEY", "ollama")
    prov = ProviderConfig(
        name="default", type="openai", base_url=_BASE, api_key_env="OPENAI_API_KEY",
        extra_body={"chat_template_kwargs": {"enable_thinking": False}}, max_tokens=4096,
    )
    llm = build_client_for_provider(prov, _MODEL, enable_thinking=False)

    over = _verify(llm, _OVERCLAIM)
    legit = _verify(llm, _LEGIT)
    print(f"model={_MODEL}")
    print(f"OVER-CLAIM (internal CV)  -> demoted: {over}    (expect: {{external_validation}})")
    print(f"LEGIT (external cohort)   -> demoted: {legit}    (expect: set())")

    ok = ("external_validation" in over) and (legit == set())
    print("RESULT:", "PASS ✓" if ok else "FAIL ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
