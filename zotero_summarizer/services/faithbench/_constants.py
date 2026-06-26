"""Pinned defaults + tunable constants for the faithfulness mini-benchmark.

``DEFAULT_JUDGE_MODEL`` follows the ARE/Gaia2 "pinned judge model" discipline:
one module-level, fully-qualified model id that every consumer (CLI flag
default, judge engine, report metadata) imports — never a floating alias like
``sota`` whose target can silently change and break score comparability. The
id must be one the judge endpoint actually serves (checked against
``GET /v1/models`` on api.kather.ai at pin time).
"""
from __future__ import annotations

# --- judge / builder endpoint (OpenAI-compatible, remote) -------------------
# Env var NAMES (never values). The same endpoint serves the QA builder and
# the equivalence judge; both default to the pinned model below.
DEFAULT_JUDGE_BASE_URL_ENV = "CUSTOM_BASE_URL"
DEFAULT_JUDGE_API_KEY_ENV = "CUSTOM_API_KEY"
# Deliberately NOT the qwen3.6-35b family (that's the model under test —
# self-judging bias) and NOT the floating "sota" alias.
DEFAULT_JUDGE_MODEL = "Qwen3.5-397B-A17B-FP8"
# Reasoning model: the thinking phase shares this budget, so it needs the same
# roomy 16384 the provider registry documents (models/providers.py) — at 2048
# the visible output is empty because thinking eats the whole allowance.
JUDGE_MAX_TOKENS = 16384

# --- benchmark build ---------------------------------------------------------
DEFAULT_N_PAPERS = 8
DEFAULT_QA_PER_PAPER = 5
DEFAULT_TRAPS_PER_PAPER = 2
MIN_PAPER_CHARS = 10_000          # papers shorter than this are skipped
MAX_GOLD_SPAN_CHARS = 120         # candidate answers longer than this are dropped
QA_WINDOW_CHARS = 6_000           # text window size fed to the QA builder
QA_MAX_WINDOWS = 3                # evenly-spaced windows per paper

# --- run (model under test) --------------------------------------------------
CHUNK_CHARS = 1_200               # retrieval condition: chunk size
CHUNK_OVERLAP = 200
RETRIEVAL_TOP_K = 6               # chunks given to the answerer
CLAIM_JUDGE_TOP_K = 8             # chunks given to the claim-support judge

# --- judge ladder thresholds ---------------------------------------------------
NUMERIC_REL_TOL = 1e-2            # relative tolerance for float gold answers
MAX_CONTAINMENT_ANSWER_CHARS = 300  # anti-gaming cap for span-containment pass
