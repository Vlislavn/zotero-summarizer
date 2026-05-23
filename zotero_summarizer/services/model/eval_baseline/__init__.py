"""Phase 1.16 Step 0 — baseline + ceiling measurement framework.

Public API:

- :func:`run_baseline` — 5×5 repeated stratified K-fold CV + BCa bootstrap CIs
- :func:`run_learning_curve` — stratified-subsample sweep over n_train
- :func:`report_to_dict`, :func:`learning_curve_to_dict` — JSON serialization
- :func:`load_golden_rows` — read the golden CSV

No model training, no model changes — pure measurement. See the plan at
``.claude/plans/idea-for-my-zotero-summarizer-harmonic-summit.md`` for the
methodology, statistical-test choices, and ship criteria.
"""

from zotero_summarizer.services.model.eval_baseline._featurize import load_golden_rows
from zotero_summarizer.services.model.eval_baseline._metrics import (
    PRIORITY_BIN_EDGES,
    PRIORITY_NAMES,
    FoldMetrics,
    priority_from_continuous,
)
from zotero_summarizer.services.model.eval_baseline._runners import (
    DEFAULT_LEARNING_CURVE_FRACTIONS,
    BaselineReport,
    LearningCurvePoint,
    LearningCurveReport,
    MetricCI,
    run_baseline,
    run_learning_curve,
)
from zotero_summarizer.services.model.eval_baseline._serialize import (
    learning_curve_to_dict,
    report_to_dict,
)

__all__ = [
    "run_baseline",
    "run_learning_curve",
    "report_to_dict",
    "learning_curve_to_dict",
    "load_golden_rows",
    "BaselineReport",
    "LearningCurveReport",
    "LearningCurvePoint",
    "FoldMetrics",
    "MetricCI",
    "PRIORITY_BIN_EDGES",
    "PRIORITY_NAMES",
    "DEFAULT_LEARNING_CURVE_FRACTIONS",
    "priority_from_continuous",
]
