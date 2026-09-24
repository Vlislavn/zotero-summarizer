"""Training rows and conservative reuse identity, shared by loader and daemon."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from importlib.metadata import distributions
from pathlib import Path
from typing import Any, NamedTuple

from zotero_summarizer.services.golden.hybrid_gt import apply_hybrid
from zotero_summarizer.services.golden.csv_store import read_snapshot
from zotero_summarizer.services.model import classifier_const as const
from zotero_summarizer.services.model.classifier_features import current_feature_year
from zotero_summarizer.services.model.tune import load_tuned_params


_TRAINING_COLUMNS = (
    "item_key", "title", "abstract", "doi", "year", "venue", "authors",
    "gold_priority_final", "gold_inferred_relevance", "gold_signal_tier",
    "annotation_count", "note_count", "in_trash", "days_since_added",
)


class TrainingInputs(NamedTuple):
    rows: list[dict[str, Any]]
    csv_sha256: str
    sha256: str
    lgbm_params: dict[str, Any]
    pca_specter_dim: int | None
    feature_reference_year: int


def _implementation_sha() -> str:
    # ponytail: hash the package, not a hand-maintained dependency graph; narrow
    # the source set only if unrelated code changes cause costly retrains.
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    versions = sorted((d.metadata["Name"], d.version) for d in distributions())
    digest.update(json.dumps([sys.version, versions]).encode())
    return digest.hexdigest()


def _corpus_rows(path: Path) -> dict[str, list]:
    """Hash authoritative corpus/goal rows, not mutable lookup-cache churn."""
    rows: dict[str, list] = {"corpus_embeddings": [], "goal_embeddings": []}
    if not path.exists():
        return rows
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("BEGIN")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, key in (("corpus_embeddings", "item_id"), ("goal_embeddings", "goal")):
            if table in tables:
                rows[table] = conn.execute(f"SELECT * FROM {table} ORDER BY {key}").fetchall()
    return rows


def load_training_inputs(
    golden_csv: Path,
    *,
    classifier_name: str,
    corpus_db_path: Path,
    goals_config: Any,
    n_folds: int,
    pca_dim: int,
    triage_db_path: Path | None = None,
) -> TrainingInputs:
    """Read CSV once for both rows and provenance; overlay the actual verdicts.

    Missing legacy identity means stale, never a CSV-only compatibility match.
    Reads fail loudly. This is not a transaction across CSV, SQLite and config.
    """
    rows, csv_sha = read_snapshot(golden_csv)
    reference_year = current_feature_year()
    if triage_db_path is not None:
        rows = apply_hybrid(rows, triage_db_path)
    config = None if goals_config is None else goals_config.model_dump(
        mode="json", include={"research_goals", "corpus", "prestige"},
    )
    corpus_enabled = config is not None and config["corpus"]["enabled"]
    tuned = load_tuned_params() if classifier_name == "lightgbm" else ({}, None)
    identity = {
        "feature_reference_year": reference_year,
        "rows": [{key: row.get(key, "") for key in _TRAINING_COLUMNS} for row in rows],
        "config": config,
        "classifier": classifier_name, "n_folds": n_folds, "pca_dim": pca_dim,
        "tuned": tuned if classifier_name == "lightgbm" else None,
        "implementation": _implementation_sha(),
        "encoder": [const.SPECTER2_MODEL_NAME, const.SPECTER2_MODEL_REVISION,
                    const.SPECTER2_ADAPTER_NAME, const.SPECTER2_ADAPTER_REVISION],
        "corpus": _corpus_rows(corpus_db_path) if corpus_enabled else None,
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return TrainingInputs(rows, csv_sha, digest, *tuned, reference_year)
