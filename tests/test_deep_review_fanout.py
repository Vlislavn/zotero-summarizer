"""``deep_review_fleet_concurrency`` — the N-paper deep-review fan-out width.

Serial (1) for a local provider; for a remote one it fans out to the work on hand,
capped by the provider's ``max_sub_concurrency`` (else all N). The regression that
matters: it must be INDEPENDENT of the global ``TRIAGE_JOB_CONCURRENCY`` knob — that
exists to serialise LOCAL triage for RAM safety, and pinning it to ``1`` (as the user's
.env does) used to throttle a genuinely remote 5-pick batch to serial.
"""
from __future__ import annotations

from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.services._common import deep_review_fleet_concurrency


def _remote(max_sub=None):
    return ProviderConfig(
        name="kather", base_url="https://api.kather.ai/v1", api_key_env="K", max_sub_concurrency=max_sub
    )


def _local():
    return ProviderConfig(name="mlx", base_url="http://127.0.0.1:8080/v1", api_key_env="K")


def test_local_provider_is_serial():
    assert deep_review_fleet_concurrency(_local(), 5) == 1


def test_remote_with_cap_fans_out_to_the_cap():
    assert deep_review_fleet_concurrency(_remote(max_sub=4), 5) == 4


def test_remote_cap_never_exceeds_work_on_hand():
    assert deep_review_fleet_concurrency(_remote(max_sub=4), 2) == 2


def test_remote_without_cap_fans_out_to_all_n():
    assert deep_review_fleet_concurrency(_remote(), 5) == 5


def test_none_provider_falls_to_remote_all_n():
    assert deep_review_fleet_concurrency(None, 5) == 5


def test_independent_of_triage_job_concurrency(monkeypatch):
    # The whole point: a remote batch must NOT read TRIAGE_JOB_CONCURRENCY (pinned to 1
    # in .env for local-RAM triage). Make settings() blow up — the width still resolves.
    import zotero_summarizer.services._common as common

    def _boom():
        raise AssertionError("deep_review_fleet_concurrency must not read the triage knob")

    monkeypatch.setattr(common, "settings", _boom)
    assert deep_review_fleet_concurrency(_remote(max_sub=4), 5) == 4
    assert deep_review_fleet_concurrency(_local(), 5) == 1
