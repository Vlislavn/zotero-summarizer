"""Saved news crosses the real CLI/store/publisher with only external gate fakes."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from html import escape
from pathlib import Path

import pytest

from zotero_summarizer import cli
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import migrations


INTERESTS = ("agent checkpoints", "биомедицинские агенты", "evaluation harness")
MODEL = "BAAI/bge-reranker-v2-m3"
SCORES = {
    "telegram:101:7": (0.10, 0.93, 0.20),
    "telegram:101:8": (0.04, 0.06, 0.02),
    "telegram:202:7": (0.80, 0.30, 0.40),
    "telegram:202:8": (0.65, 0.72, 0.88),
    "telegram:202:9": (0.61, 0.55, 0.77),
}
PAPER_TABLES = ("rss_feeds", "rss_items", "processed_feed_items", "triage_results",
                "label_verdicts", "pending_changes", "user_feedback")


@pytest.fixture(autouse=True)
def _loaded_source_receipt(tmp_path):
    yield
    root = Path(__file__).resolve().parents[1]
    receipt = {}
    for name, module in sorted(sys.modules.copy().items()):
        if not name.startswith(("zotero_summarizer.cli", "zotero_summarizer.storage",
                                "zotero_summarizer.services.research_feed",
                                "zotero_summarizer.services.model.reranker",
                                "zotero_summarizer.services.llm.factory")):
            continue
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(root), f"loaded source outside assigned root: {path}"
        receipt[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (tmp_path / "loaded-source.json").write_text(json.dumps(receipt, indent=2) + "\n")


@pytest.mark.parametrize("owner", ["parent_root", "telegram_directory", "state_file"])
def test_saved_news_refuses_aliased_output_owner_before_inputs_or_models(tmp_path, monkeypatch, owner):
    from unittest.mock import Mock
    from zotero_summarizer.services.research_feed import telegram

    settings = _project(tmp_path)
    parent = settings.data_dir / "research_feed"
    directory = parent / "telegram"
    outside = settings.data_dir / "not-news-owned"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"preserve owned outside data")
    if owner == "parent_root":
        parent.rename(settings.data_dir / "retained-parent")
        parent.symlink_to(outside, target_is_directory=True)
        link = parent
    elif owner == "telegram_directory":
        directory.symlink_to(outside, target_is_directory=True)
        link = directory
    else:
        directory.mkdir()
        link = directory / "state.json"
        link.symlink_to(sentinel)
    before = settings.triage_db_path.read_bytes()
    forbidden = Mock(side_effect=AssertionError("output admission must precede saved input/model work"))
    monkeypatch.setattr(telegram, "read_posts", forbidden)
    monkeypatch.setattr(telegram, "load_profile", forbidden)
    monkeypatch.setattr(telegram, "read_config", forbidden)
    monkeypatch.setattr(telegram, "_process", forbidden)

    with pytest.raises(ValueError, match="symlink"):
        telegram.run_saved(settings, input_jsonl=settings.project_root / "not-read.jsonl", collect_only=True)
    forbidden.assert_not_called()
    assert link.is_symlink() and set(outside.iterdir()) == {sentinel}
    assert sentinel.read_bytes() == b"preserve owned outside data"
    assert settings.triage_db_path.read_bytes() == before


def test_saved_news_publication_rechecks_parent_owner_before_projection(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from zotero_summarizer.services.research_feed import telegram

    settings = _project(tmp_path)
    parent = settings.data_dir / "research_feed"
    parent.rename(settings.data_dir / "retained-parent")
    outside = settings.data_dir / "not-news-owned"
    outside.mkdir()
    parent.symlink_to(outside, target_is_directory=True)
    forbidden = Mock(side_effect=AssertionError("publication owner recheck must precede projection"))
    monkeypatch.setattr(telegram.telegram_render, "payload", forbidden)

    with pytest.raises(ValueError, match="symlink"):
        telegram._publish(settings, [], {"counts": {}}, None, 1, True)
    forbidden.assert_not_called()
    assert parent.is_symlink() and not list(outside.iterdir())


def _key(post):
    return f"telegram:{post['channel_id']}:{post['message_id']}"


def _posts():
    rows = [
        (101, 7, "🧪 Агент восстанавливает контрольные точки & память."),
        (101, 8, "Local football results have no research claim."),
        (202, 7, "Биомедицинские агенты требуют проверки."),
        (202, 8, "Agent evaluation found ambiguous evidence."),
        (202, 9, "A harness benchmark awaits examination."),
        (101, 9, ""),
    ]
    return [{
        "schema_version": 1, "channel_id": channel, "message_id": message,
        "username": f"fixture_{channel}", "permalink": f"https://example.test/tg/{channel}/{message}",
        "text": text, "published_at": f"2026-10-02T10:0{index}:00Z", "updated_at": None,
        "urls": [f"https://example.test/source/{channel}/{message}"] if text else [],
        "skip_reason": None if text else "media_only",
    } for index, (channel, message, text) in enumerate(rows)]


def _write_inputs(path, posts):
    path.write_text("".join(json.dumps(post, ensure_ascii=False) + "\n" for post in posts),
                    encoding="utf-8")


def _project(root):
    settings = Settings.load(project_root=root)
    migrations.run_migrations(settings.triage_db_path, "triage", migrations.TRIAGE_MIGRATIONS)
    profile = settings.data_dir / "research_feed" / "profile.json"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(json.dumps({
        "schema_version": 1, "themes": list(INTERESTS[:2]), "projects": [INTERESTS[2]],
        "shortlist_budget": 4, "card_budget": 1, "telegram_channels": [],
    }, ensure_ascii=False), encoding="utf-8")
    provider = {"name": "news-default", "base_url": "https://provider.example.test/v1",
                "api_key_env": "FIXTURE_API_KEY", "max_tokens": 256}
    other = {**provider, "name": "unused-feed"}
    settings.config_path.write_text(json.dumps({
        "research_goals": ["saved news"], "triage_criteria": ["source-grounded relevance"],
        "relevance_scale": {str(i): f"fixture band {i}" for i in range(1, 6)},
        "summary_structure": ["summary"],
        "llm": {"draft_model": "unused-legacy", "refine_model": "unused-legacy",
                "api_base": "https://legacy.example.test/v1", "api_key_env": "FIXTURE_API_KEY"},
        "llm_routing": {"providers": [provider, other],
                        "default": {"provider": "news-default", "model": "fixture-news"},
                        "feed": {"provider": "unused-feed", "model": "must-not-use"}},
    }), encoding="utf-8")
    return settings


class ExternalGates:
    def __init__(self, posts):
        self.posts = {_key(post): post for post in posts}
        self.trace = []
        self.phase = "first"
        self.blocked = False

    def get_reranker(self, model_name):
        assert not self.blocked, "read/collect mode must not construct a model"
        assert model_name == MODEL
        self.trace.append(("model", model_name))
        return self

    def is_ready(self):
        return True

    def is_loading(self):
        return False

    def ensure_loaded_async(self):
        assert not self.blocked

    def rerank(self, query, pairs, top_n):
        assert not self.blocked
        assert query in INTERESTS
        assert top_n == len(pairs)
        scores = []
        for key, text in pairs:
            assert text == self.posts[key]["text"] and text
            score = SCORES[key][INTERESTS.index(query)]
            self.trace.append(("ml", key, text, query, score))
            scores.append((key, score))
        return sorted(scores, key=lambda row: row[1], reverse=True)

    def build_client(self, provider, model, **kwargs):
        assert not self.blocked, "read/collect mode must not construct a provider"
        assert provider.name == "news-default" and model == "fixture-news"
        self.trace.append(("provider", provider.name, model))
        return self

    def pydantic_prompt(self, prompt, pydantic_model, **kwargs):
        assert not self.blocked
        matches = [key for key, post in self.posts.items() if post["text"] and
                   f"<untrusted_input>{escape(post['text'], quote=False)}</untrusted_input>" in prompt]
        assert len(matches) == 1, "prompt must carry one escaped original post"
        key = matches[0]
        text = self.posts[key]["text"]
        seen = {row[3] for row in self.trace if row[0] == "ml" and row[1:3] == (key, text)}
        assert seen == set(INTERESTS), "every interest must finish ML before this LLM post"
        self.trace.append(("llm", key, text, self.phase))
        if key == "telegram:202:8" and self.phase == "first":
            raise RuntimeError("synthetic provider failure")
        decision = {"telegram:101:7": "keep", "telegram:101:8": "drop",
                    "telegram:202:7": "uncertain", "telegram:202:8": "keep",
                    "telegram:202:9": "uncertain"}[key]
        if key == "telegram:101:7" and self.phase == "edited":
            decision = "drop"
        return pydantic_model.model_validate({
            "decision": decision, "matched_interests": [INTERESTS[1]],
            "reason": f"Synthetic reason for {key}", "evidence_quote": text,
            "summary": f"Synthetic summary for {key} ({self.phase})" if decision == "keep" else "",
        })


def _command(argv, capsys):
    try:
        result = cli.main(argv)
    except SystemExit as exc:
        pytest.fail(f"saved-news CLI rejected its requested mode (argparse exit {exc.code})")
    assert result == 0
    return capsys.readouterr().out


def _run(root, capsys, *options):
    argv = ["research-feed", "run", "--source", "telegram", "--project-root", str(root),
            "--shortlist-budget", "4", "--card-budget", "1", *map(str, options)]
    return json.loads(_command(argv, capsys))


def _report(receipt):
    json_path, md_path = Path(receipt["json_path"]), Path(receipt["markdown_path"])
    assert json_path.parent == md_path.parent and json_path.is_file() and md_path.is_file()
    return json.loads(json_path.read_text(encoding="utf-8")), md_path.read_text(encoding="utf-8")


def _stored(settings):
    with closing(sqlite3.connect(f"file:{settings.triage_db_path}?mode=ro", uri=True)) as conn:
        rows = conn.execute("SELECT channel_id, message_id, content_hash, evaluation_json "
                            "FROM research_feed_telegram_posts").fetchall()
        cursors = conn.execute("SELECT confirmed_message_id FROM research_feed_telegram_channels").fetchall()
        assert all(row[0] in (None, 0) for row in cursors), "JSONL cannot certify a live cursor"
        assert conn.execute("SELECT version FROM schema_migrations WHERE namespace='triage'").fetchone()[0] >= 11
        assert {name: conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                for name in PAPER_TABLES} == dict.fromkeys(PAPER_TABLES, 0)
    return {f"telegram:{channel}:{message}": {
        "content_hash": content_hash, "evaluation": json.loads(evaluation) if evaluation else None,
    } for channel, message, content_hash, evaluation in rows}


def _assert_scores(records, stored):
    by_key = {record["source_id"]: record for record in records}
    assert len(by_key) == len(records) == 6
    for key, scores in SCORES.items():
        ml = by_key[key]["ml"]
        assert ml["score"] == max(scores)
        assert ml["threshold"] is None and ml["hard_gate"] is False
        assert ml["truncated"] is None and ml["revision"] is None
        assert stored[key]["evaluation"]["ml"]["score"] == max(scores)
    return by_key


def _bundle_bytes(receipt):
    return {path: path.read_bytes() for path in Path(receipt["json_path"]).parent.iterdir()
            if path.is_file()}


def _remember_bundle(immutable, receipt):
    assert all(path.read_bytes() == content for path, content in immutable.items()), "prior bundle changed"
    current = _bundle_bytes(receipt)
    assert all(path not in immutable or immutable[path] == content
               for path, content in current.items()), "report path reused with different bytes"
    for path, content in current.items():
        immutable.setdefault(path, content)


@pytest.mark.parametrize("reuse_path", [True, False])
def test_bundle_snapshots_reject_rewritten_history(tmp_path, reuse_path):
    first = tmp_path / "first" / "report.json"
    first.parent.mkdir()
    first.write_bytes(b"original")
    immutable = _bundle_bytes({"json_path": str(first)})
    earliest = immutable.copy()
    first.write_bytes(b"rewritten")
    later = first if reuse_path else tmp_path / "later" / "report.json"
    later.parent.mkdir(exist_ok=True)
    later.write_bytes(b"rewritten")

    with pytest.raises(AssertionError):
        _remember_bundle(immutable, {"json_path": str(later)})
    assert immutable == earliest


def test_bundle_snapshots_allow_identical_reuse_and_fresh_paths(tmp_path):
    first = tmp_path / "first" / "report.json"
    first.parent.mkdir()
    first.write_bytes(b"original")
    immutable = _bundle_bytes({"json_path": str(first)})

    _remember_bundle(immutable, {"json_path": str(first)})
    assert immutable == {first: b"original"}
    later = tmp_path / "later" / "report.json"
    later.parent.mkdir()
    later.write_bytes(b"later")
    _remember_bundle(immutable, {"json_path": str(later)})
    assert immutable == {first: b"original", later: b"later"}
    assert all(path.read_bytes() == content for path, content in immutable.items())


def _first_run(settings, input_path, gates, capsys):
    receipt = _run(settings.project_root, capsys, "--input-jsonl", input_path,
                   "--from", "2026-10-01", "--to", "2026-10-08")
    payload, markdown = _report(receipt)
    stored = _stored(settings)
    assert set(stored) == set(gates.posts) and len(stored) == 6
    assert all(len(row["content_hash"]) == 64 for row in stored.values())
    records = _assert_scores(payload["posts"], stored)
    assert {key: row["state"] for key, row in records.items()} == {
        "telegram:101:7": "keep", "telegram:101:8": "drop", "telegram:202:7": "uncertain",
        "telegram:202:8": "error", "telegram:202:9": "pending", "telegram:101:9": "skipped",
    }
    for key, post in gates.posts.items():
        assert records[key]["post"]["text"] == post["text"]
        assert records[key]["post"]["permalink"] == post["permalink"]
    counts = payload["metadata"]["counts"]
    assert {key: counts[key] for key in (
        "fetched", "new", "updated", "skipped", "ml_scored", "llm_attempts", "llm_processed",
        "kept", "uncertain", "pending", "errors", "cards_generated",
    )} == dict(fetched=6, new=6, updated=0, skipped=1, ml_scored=5, llm_attempts=4,
               llm_processed=3, kept=1, uncertain=1, pending=1, errors=1, cards_generated=1)
    assert [row[1] for row in gates.trace if row[0] == "llm"] == list(SCORES)[:4]
    assert {row[1:4] for row in gates.trace if row[0] == "ml"} == {
        (key, gates.posts[key]["text"], interest) for key in SCORES for interest in INTERESTS
    }
    assert [card["source_id"] for card in payload["cards"]] == ["telegram:101:7"]
    card = payload["cards"][0]
    assert card["permalink"] == gates.posts["telegram:101:7"]["permalink"]
    assert card["evidence_quote"] == gates.posts["telegram:101:7"]["text"]
    assert card["summary"] == "Synthetic summary for telegram:101:7 (first)"
    assert card["reason"] == "Synthetic reason for telegram:101:7"
    assert all(card[field] in markdown for field in ("permalink", "summary", "reason"))
    assert [row["source_id"] for row in payload["uncertain"]] == ["telegram:202:7"]
    assert "uncertain" in markdown.lower() and gates.posts["telegram:202:7"]["permalink"] in markdown
    return receipt, stored, card["summary"]


def _replay(settings, input_path, gates, capsys):
    start = len(gates.trace)
    gates.phase = "replay"
    receipt = _run(settings.project_root, capsys, "--input-jsonl", input_path)
    payload, _ = _report(receipt)
    stored = _stored(settings)
    _assert_scores(payload["posts"], stored)
    assert [row[1] for row in gates.trace[start:] if row[0] == "llm"] == [
        "telegram:202:8", "telegram:202:9",
    ]
    assert all(row[1] in {"telegram:202:8", "telegram:202:9"}
               for row in gates.trace[start:] if row[0] == "ml")
    assert payload["metadata"]["counts"]["llm_attempts"] == 2
    assert payload["metadata"]["counts"]["new"] == 0
    assert len(payload["cards"]) == 1
    return receipt, stored


def _collect_and_view(settings, input_path, gates, capsys, before, old_summary):
    posts = list(gates.posts.values())
    posts[0]["text"] = "🧪 Исправленный пост описывает другой эксперимент."
    posts[0]["updated_at"] = "2026-10-03T10:00:00Z"
    _write_inputs(input_path, posts)
    gates.blocked = True
    start = len(gates.trace)
    collected = _run(settings.project_root, capsys, "--input-jsonl", input_path, "--collect-only")
    exported = [json.loads(line) for line in Path(collected["inputs_path"]).read_text().splitlines()]
    assert {_key(post): post["text"] for post in exported} == {_key(post): post["text"] for post in posts}
    updated = _stored(settings)
    key = "telegram:101:7"
    assert updated[key]["content_hash"] != before[key]["content_hash"]
    assert updated[key]["evaluation"] is None
    assert {k: v for k, v in updated.items() if k != key} == {k: v for k, v in before.items() if k != key}
    cached = _run(settings.project_root, capsys, "--cached-only")
    payload, markdown = _report(cached)
    record = next(row for row in payload["posts"] if row["source_id"] == key)
    assert record["state"] == "pending" and record["post"]["text"] == posts[0]["text"]
    assert old_summary not in json.dumps(payload, ensure_ascii=False) and old_summary not in markdown
    view = _command(["research-feed", "view", "--report", cached["markdown_path"],
                     "--project-root", str(settings.project_root)], capsys)
    assert view.rstrip("\n") == markdown.rstrip("\n")
    assert gates.trace[start:] == []
    assert _stored(settings) == updated
    return cached


def test_saved_news_cli_orders_gates_preserves_replay_and_invalidates_edits(tmp_path, monkeypatch, capsys):
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services.model import reranker

    posts = _posts()
    settings = _project(tmp_path)
    input_path = tmp_path / "authored-news.jsonl"
    _write_inputs(input_path, posts)
    gates = ExternalGates(posts)
    monkeypatch.setattr(reranker, "get_reranker", gates.get_reranker)
    monkeypatch.setattr(factory, "build_client_for_provider", gates.build_client)

    first, first_rows, old_summary = _first_run(settings, input_path, gates, capsys)
    immutable = _bundle_bytes(first)
    replay, replay_rows = _replay(settings, input_path, gates, capsys)
    assert {key: row for key, row in replay_rows.items() if key in list(SCORES)[:3]} == {
        key: row for key, row in first_rows.items() if key in list(SCORES)[:3]
    }
    _remember_bundle(immutable, replay)
    cached = _collect_and_view(settings, input_path, gates, capsys, replay_rows, old_summary)
    _remember_bundle(immutable, cached)
    gates.blocked = False
    gates.phase = "edited"
    start = len(gates.trace)
    edited = _run(settings.project_root, capsys, "--input-jsonl", input_path)
    payload, markdown = _report(edited)
    assert [row[1] for row in gates.trace[start:] if row[0] == "llm"] == ["telegram:101:7"]
    record = next(row for row in payload["posts"] if row["source_id"] == "telegram:101:7")
    assert record["state"] == "drop" and record["post"]["text"] == posts[0]["text"]
    assert old_summary not in json.dumps(payload, ensure_ascii=False) and old_summary not in markdown
    _assert_scores(payload["posts"], _stored(settings))
    assert all(path.read_bytes() == content for path, content in immutable.items())
    assert not settings.golden_csv_path.exists() and not settings.golden_jsonl_path.exists()
    assert not settings.corpus_db_path.exists() and not settings.interaction_log_path.exists()
