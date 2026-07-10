"""Model-free pure helpers shared by the GLiNER2 Phase-0 benchmark scripts
(bench_gliner2.py = the encoder; bench_llm_ner.py + bench_llm_params.py = the LLM
'against what'). Kept here so both sides score with the SAME grader and neither
script has to import the other. stdlib only — no gliner2, no product import, no
network; all functions are deterministic and unit-checked by
bench_gliner2.py --selfcheck.
"""
from __future__ import annotations

from typing import Any


# ---- tiny stats ------------------------------------------------------------------

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _norm(s: Any) -> str:
    return " ".join(str(s).lower().split())


# ---- NER / set graders -----------------------------------------------------------

def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
            "tp": tp, "fp": fp, "fn": fn}


def score_ner(pred: set[str], gold: set[str], lenient: bool) -> tuple[int, int, int]:
    """(tp, fp, fn) over one item. strict = exact normalized match; lenient = a pred
    counts as TP if it substring-overlaps some gold entity either way (absorbs
    modifier drift like 'metastatic melanoma' vs 'melanoma')."""
    if not lenient:
        tp = len(pred & gold)
        return tp, len(pred - gold), len(gold - pred)
    matched_gold: set[str] = set()
    tp = 0
    for p in pred:
        hit = next((g for g in gold if p == g or p in g or g in p), None)
        if hit is not None:
            tp += 1
            matched_gold.add(hit)
    fp = len(pred) - tp
    fn = len(gold - matched_gold)
    return tp, fp, fn


# ---- 0c PaperParameters schema (mirrors models.triage.PaperParameters; inlined) --

PARAM_FIELDS = ["dataset", "baselines", "sample_size", "metrics", "architecture", "external_validation"]
LIST_FIELDS = {"baselines", "metrics"}
BOOL_FIELDS = {"external_validation"}
# GLiNER2 field-spec grammar: "name::dtype::description" / "[a|b]" enumerates choices.
PARAM_STRUCT: dict[str, list[str]] = {
    "paper": [
        "dataset::str::name of the dataset(s) used for training or evaluation",
        "baselines::list::baseline methods or models compared against",
        "sample_size::str::number of samples, patients, images, or records",
        "metrics::list::evaluation metrics reported such as AUC F1 accuracy BLEU",
        "architecture::str::the core model architecture or method name",
        "external_validation::[yes|no]::whether validated on an external independent dataset",
    ],
}
# A deliberately parameter-LESS input: the faithbench abstention contract says a
# paper with no extractable parameters must yield empties, never a fabricated guess.
ABSTAIN_FIXTURE = (
    "This position paper argues that the machine-learning community should prioritise "
    "reproducibility and open peer review. We present no new dataset, run no experiments, "
    "train no model, and report no quantitative results; our sole contribution is a "
    "conceptual framework and a call to action for the field."
)


def _yesno(val: Any) -> bool | None:
    """yes/no/bool → tri-state; anything else (null, "", unknown) → None (abstain)."""
    if isinstance(val, bool):
        return val
    s = _norm(val)
    if s in ("yes", "true"):
        return True
    if s in ("no", "false"):
        return False
    return None


def parse_params(raw: Any) -> dict[str, Any]:
    """Normalize extract_json output {'paper':[{...}]} (or {'paper':{...}}) to the
    6-field PaperParameters contract. Missing/null → '' | [] | None (abstention)."""
    rec: dict[str, Any] = {}
    if isinstance(raw, dict):
        v = raw.get("paper", raw)
        if isinstance(v, list):
            rec = v[0] if v and isinstance(v[0], dict) else {}
        elif isinstance(v, dict):
            rec = v
    out: dict[str, Any] = {}
    for f in PARAM_FIELDS:
        val = rec.get(f)
        if f in LIST_FIELDS:
            out[f] = sorted({_norm(x) for x in val if str(x).strip()}) if isinstance(val, list) else []
        elif f in BOOL_FIELDS:
            out[f] = _yesno(val)
        else:
            out[f] = _norm(val) if val not in (None, "") else ""
    return out


def has_value(field: str, v: Any) -> bool:
    """Did the extractor emit a value for this field (vs abstain)?"""
    if field in LIST_FIELDS:
        return bool(v)
    if field in BOOL_FIELDS:
        return v is not None
    return bool(v)


def value_match(field: str, a: Any, b: Any) -> bool:
    """Do two emitted values agree? bool=exact; list=any lenient-overlap member;
    str=substring-overlap either way (absorbs 'ChestX-ray14' vs 'chestx-ray')."""
    if field in BOOL_FIELDS:
        return a == b
    if field in LIST_FIELDS:
        tp, _, _ = score_ner(set(a), set(b), lenient=True)
        return tp > 0
    return bool(a) and bool(b) and (a == b or a in b or b in a)


def compare_params(enc: dict[str, Any], llm: dict[str, Any]) -> dict[str, str]:
    """Per-field verdict of encoder vs the LLM it would replace: both_match /
    both_mismatch / enc_only (over-emission) / llm_only (miss) / both_absent."""
    r: dict[str, str] = {}
    for f in PARAM_FIELDS:
        pe, pl = has_value(f, enc[f]), has_value(f, llm[f])
        if pe and pl:
            r[f] = "both_match" if value_match(f, enc[f], llm[f]) else "both_mismatch"
        elif pe:
            r[f] = "enc_only"
        elif pl:
            r[f] = "llm_only"
        else:
            r[f] = "both_absent"
    return r
