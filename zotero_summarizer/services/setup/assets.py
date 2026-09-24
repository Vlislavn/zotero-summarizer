"""Shared Hugging Face cache/prefetch/offline-load checks."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from typing import Any
from unittest.mock import patch

from zotero_summarizer.services._common import read_config


def model_targets(config: Any) -> list[tuple[str, str, str | None]]:
    from zotero_summarizer.services.model.classifier_const import (
        SPECTER2_ADAPTER_NAME,
        SPECTER2_ADAPTER_REVISION,
        SPECTER2_MODEL_NAME,
        SPECTER2_MODEL_REVISION,
    )

    targets = [
        ("gate encoder", SPECTER2_MODEL_NAME, SPECTER2_MODEL_REVISION),
        ("gate adapter", SPECTER2_ADAPTER_NAME, SPECTER2_ADAPTER_REVISION),
        ("corpus embeddings", config.corpus.embedding_model, None),
        ("search reranker", config.corpus.reranker_model, None),
    ]
    if config.quality_review.shadow_claim_check:
        from zotero_summarizer.services.model.claim_checker import hf_repo_for

        targets.append(("claim checker", hf_repo_for(config.quality_review.claim_check_model), None))
    return targets


def cache_report(targets: list[tuple[str, str, str | None]]) -> list[dict[str, Any]]:
    from huggingface_hub import scan_cache_dir

    sizes = {}
    for repo in scan_cache_dir().repos:
        sizes[repo.repo_id, None] = int(repo.size_on_disk)
        for revision in repo.revisions:
            sizes[repo.repo_id, revision.commit_hash] = int(revision.size_on_disk)
    return [
        {"label": label, "repo_id": repo, "cached": sizes.get((repo, revision), 0) > 0,
         "size_mb": round(sizes.get((repo, revision), 0) / 1e6, 1)}
        for label, repo, revision in targets
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


def offline_probe_main(settings: Any) -> dict[str, Any]:
    """Deny and record outbound socket attempts while loading cached assets."""
    attempts: list[str] = []

    def deny_connection(sock: socket.socket, address: Any) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            attempts.append(str(address))
            raise OSError("network connection attempted during offline asset load")
        return original_connect(sock, address)

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def deny_connect_ex(sock: socket.socket, address: Any) -> int:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            attempts.append(str(address))
            raise OSError("network connection attempted during offline asset load")
        return original_connect_ex(sock, address)

    with patch.object(socket.socket, "connect", deny_connection), patch.object(socket.socket, "connect_ex", deny_connect_ex):
        try:
            report = asset_report(settings, load=True)
        except OSError:
            if not attempts:
                raise
            return {"offline_ready": False, "loadable": False,
                    "models": [], "network_attempts": attempts}
    return {**report, "network_attempts": attempts}


def offline_asset_report(settings: Any, *, timeout: int = 300) -> dict[str, Any]:
    """Load in a fresh cache-only process and record any blocked TCP attempts."""
    script = (
        "import json,sys; from zotero_summarizer.settings import Settings; "
        "from zotero_summarizer.services.setup.assets import offline_probe_main; "
        "print(json.dumps(offline_probe_main(Settings.load(project_root=sys.argv[1]))))"
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
