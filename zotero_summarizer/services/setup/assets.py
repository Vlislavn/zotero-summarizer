"""Shared Hugging Face cache/prefetch/offline-load checks."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from zotero_summarizer.services._common import read_config


def model_targets(config: Any) -> list[tuple[str, str]]:
    from zotero_summarizer.services.model.classifier_const import (
        SPECTER2_ADAPTER_NAME,
        SPECTER2_MODEL_NAME,
    )

    targets = [
        ("gate encoder", SPECTER2_MODEL_NAME),
        ("gate adapter", SPECTER2_ADAPTER_NAME),
        ("corpus embeddings", config.corpus.embedding_model),
        ("search reranker", config.corpus.reranker_model),
    ]
    if config.quality_review.shadow_claim_check:
        from zotero_summarizer.services.model.claim_checker import hf_repo_for

        targets.append(("claim checker", hf_repo_for(config.quality_review.claim_check_model)))
    return targets


def cache_report(targets: list[tuple[str, str]]) -> list[dict[str, Any]]:
    from huggingface_hub import scan_cache_dir

    sizes = {repo.repo_id: int(repo.size_on_disk) for repo in scan_cache_dir().repos}
    return [
        {"label": label, "repo_id": repo, "cached": sizes.get(repo, 0) > 0,
         "size_mb": round(sizes.get(repo, 0) / 1e6, 1)}
        for label, repo in targets
    ]


def _load_assets(settings: Any, config: Any) -> None:
    from zotero_summarizer.storage.corpus import EmbeddingCache

    embedding = EmbeddingCache(settings.corpus_db_path, config.corpus.embedding_model)
    if embedding._load_model() is None:
        raise RuntimeError("corpus embedding model did not load")
    from zotero_summarizer.services.model.reranker import get_reranker

    reranker = get_reranker(config.corpus.reranker_model)
    reranker._load()
    if not reranker.is_ready():
        raise RuntimeError("search reranker did not load")
    from zotero_summarizer.services.model.classifier_embed import _load_specter2

    _load_specter2()
    if config.quality_review.shadow_claim_check:
        from zotero_summarizer.services.model.claim_checker import get_claim_checker

        checker = get_claim_checker(config.quality_review.claim_check_model)
        checker._load()
        if not checker.is_ready():
            raise RuntimeError("claim checker did not load")


def asset_report(settings: Any, *, load: bool = False) -> dict[str, Any]:
    config = read_config(settings.config_path, settings.calibration_path)
    models = cache_report(model_targets(config))
    complete = all(row["cached"] for row in models)
    if load and complete:
        _load_assets(settings, config)
    return {"offline_ready": complete, "loadable": complete if load else None, "models": models}


def prefetch_assets(settings: Any) -> dict[str, Any]:
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ.pop(key, None)
    config = read_config(settings.config_path, settings.calibration_path)
    _load_assets(settings, config)
    return asset_report(settings)


def offline_asset_report(settings: Any, *, timeout: int = 300) -> dict[str, Any]:
    """Load in a fresh cache-only process so imported libraries cannot retain online flags."""
    script = (
        "import json,sys; from zotero_summarizer.settings import Settings; "
        "from zotero_summarizer.services.setup.assets import asset_report; "
        "print(json.dumps(asset_report(Settings.load(project_root=sys.argv[1]), load=True)))"
    )
    env = os.environ.copy()
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1")
    run = subprocess.run(
        [sys.executable, "-c", script, str(settings.project_root)],
        text=True, capture_output=True, timeout=timeout, env=env,
    )
    if run.returncode:
        detail = (run.stderr or run.stdout).strip().splitlines()[-1]
        raise RuntimeError(detail)
    return json.loads(run.stdout.strip().splitlines()[-1])
