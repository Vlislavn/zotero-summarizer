"""Today ranker ``goal_cos_v1``: order eligible papers by goal-text cosine.

Evidence (``data/perf-20261107/signal-lab/signal-lab.md``): within-day AUC on the
owner's Today decisions is 0.635 for goal cosine vs 0.574 for the composite; on the
15 pilot verdicts Spearman is 0.83 vs 0.72. No metadata/quality term added signal.
This is NOT a new model: it is the shared ``rank_blend.blend_scores`` with its goal
weight raised to 1.0 and no prestige term, so ``rank_score`` is the min-max of the
stored ``aux_context.goal_sims`` max (``candidate["goal_sim"]``). A row without a
goal cosine scores 0.0 (below every evidenced row); a pool with NO goal cosine folds
back to the composite order (``blend_scores`` contract).

The ranker id is an operational setting (``settings.today_ranker``), outside
review identity.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services.model.rank_blend import blend_scores

GOAL_COS = "goal_cos_v1"
GOAL_COS_WEIGHT = 1.0  # blend_scores(goal_weight=): relevance gets 1 - weight = 0


def attach_goal_cos_keys(candidates: list[dict[str, Any]]) -> None:
    """Overwrite ``rank_score`` (and its observed cohort key) with the goal-cosine blend."""
    keys = blend_scores(
        [c["composite_score"] for c in candidates],
        [c["goal_sim"] for c in candidates],
        [None] * len(candidates),
        goal_weight=GOAL_COS_WEIGHT,
        prestige_weight=0.0,
    )
    for cand, key in zip(candidates, keys):
        cand["rank_score"] = key
        cand["selection_evidence"]["rank"] = {
            "basis": "goal_cos", "key": key, **({"goal": cand["goal_text"]} if cand.get("goal_text") else {}),
        }
