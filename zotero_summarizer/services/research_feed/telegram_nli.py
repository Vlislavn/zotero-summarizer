"""Local entailment gate for Telegram gist lines: mDeBERTa-v3 XNLI (multilingual, MIT), CPU, loaded lazily.

``entail(directory, premise, hypothesis)`` is P(premise entails hypothesis); ``None`` when the model folder is missing or cannot
load (the caller then keeps the lines unchecked). The model is a plain Hugging Face folder at ``Settings.nli_model_dir``."""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)
MAX_TOKENS = 512
_LOADED: dict[Path, object] = {}


def _load(directory: Path):
    if directory not in _LOADED:
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            torch.set_num_threads(4)
            model = AutoModelForSequenceClassification.from_pretrained(directory).eval()
            labels = {name.lower(): index for index, name in model.config.id2label.items()}
            _LOADED[directory] = (AutoTokenizer.from_pretrained(directory), model, labels["entailment"])
        except Exception as error:  # missing folder, no torch: lines stay unchecked
            log.warning("telegram gist: NLI gate unavailable (%s): %s", directory, error)
            _LOADED[directory] = None
    return _LOADED[directory]


def entail(directory: Path, premise: str, hypothesis: str) -> float | None:
    loaded = _load(directory)
    if loaded is None:
        return None
    import torch

    tokenizer, model, entailment = loaded
    encoded = tokenizer(premise, hypothesis, truncation="only_first", max_length=MAX_TOKENS, return_tensors="pt")
    with torch.no_grad():
        return float(model(**encoded).logits.softmax(-1)[0][entailment])


__all__ = ["entail"]
