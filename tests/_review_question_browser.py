"""Owned real-TCP browser fixture; reuses application-smoke bootstrap/startup policy."""
from contextlib import closing
from dataclasses import dataclass, field
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
from tempfile import mkdtemp
import threading
import time
from urllib.parse import urlsplit

import pytest

from tests._zotero_fixtures import add_library_item, build_zotero_db
from zotero_summarizer.api import app as api_app
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.runtime import get_context
from zotero_summarizer.services import lifecycle
from zotero_summarizer.services._common import read_config, write_user_config
from zotero_summarizer.services.library import paper_render
from zotero_summarizer.services.setup import bootstrap
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import feeds, repositories, rss
from zotero_summarizer.storage.feed_identity import stable_feed_key_from_parts

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/issue-validation/question-browser"
_CONNECT, _CONNECT_EX = socket.socket.connect, socket.socket.connect_ex
WAIT_SECONDS = float(os.environ.get("ZS_QUESTION_BROWSER_WAIT_SECONDS", "30"))
assert 1 <= WAIT_SECONDS <= 60
ACTION_TIMEOUT_MS = int(WAIT_SECONDS * 1000)
LIBRARY_KEY = "ITEM0001"
FEED_URL = "https://synthetic.invalid/fixture.pdf"
PAGE_COUNT_QUESTION = "How many pages are in the whole paper?"
TABLES = ("review_questions", "review_notes", "sync_changes", "sync_mutations",
          "review_note_mirror_intents", "label_verdicts")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def db_rows(path, sql, parameters=()):
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, parameters)]


def indexeddb(page):
    return page.evaluate("""async () => {
      const db = await new Promise((resolve, reject) => {
        const req = indexedDB.open('zotero-summarizer-offline', 1);
        req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error);
      });
      const tx = db.transaction(['papers', 'mutations', 'meta'], 'readonly');
      const read = name => new Promise((resolve, reject) => {
        const req = tx.objectStore(name).getAll();
        req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error);
      });
      const [papers, mutations, meta] = await Promise.all(['papers', 'mutations', 'meta'].map(read));
      db.close(); return {papers, mutations, meta, online: navigator.onLine};
    }""")


def main_world(page, expression):
    session = getattr(page, "_question_main_world_session", None)
    if session is None:
        session = page.context.new_cdp_session(page)
        page._question_main_world_session = session
        page._question_main_world_closed = False
        session.on("close", lambda _: setattr(page, "_question_main_world_closed", True))
    # Detach resets navigator.onLine in installed Chrome; context.close owns this session.
    result = session.send("Runtime.evaluate", {
        "expression": f"Promise.race([({expression})(), new Promise((_, reject) => "
                      f"setTimeout(() => reject(new Error('Main-world probe timeout')), {ACTION_TIMEOUT_MS}))])",
        "awaitPromise": True, "returnByValue": True,
    })
    assert "exceptionDetails" not in result, result
    return result["result"].get("value")


def query_state(page, item_key, probe_local=False):
    expression = """async () => {
      const key = ITEM_KEY_LITERAL;
      const probeLocal = PROBE_LOCAL_LITERAL;
      const root = document.getElementById('root');
      const field = Object.keys(root).find(name => name.startsWith('__reactContainer$'));
      const stack = [root[field]], visited = new Set();
      while (stack.length) {
        const fiber = stack.pop();
        if (!fiber || visited.has(fiber)) continue;
        visited.add(fiber);
        const client = fiber.memoizedProps?.client;
        if (client?.getQueryCache) {
          const query = client.getQueryCache().find({queryKey: ['review-detail', key]});
          if (!query) return null;
          const before = {queryKey: query.queryKey, networkMode: query.options.networkMode ?? 'online',
            fetchStatus: query.state.fetchStatus, status: query.state.status,
            isInvalidated: query.state.isInvalidated, questions: query.state.data?.questions ?? null};
          if (probeLocal) {
            if (navigator.onLine || query.queryKey[0] !== 'review-detail' || query.queryKey[1] !== key)
              throw new Error('Not the owned offline detail query');
            const detail = await query.options.queryFn();
            before.directLocalRead = {item_key: detail.item_key, questions: detail.questions, user_note: detail.user_note};
          }
          return before;
        }
        stack.push(fiber.child, fiber.sibling, fiber.alternate);
      }
      return null;
    }"""
    return main_world(page, expression.replace("ITEM_KEY_LITERAL", json.dumps(item_key))
                      .replace("PROBE_LOCAL_LITERAL", json.dumps(probe_local)))


def wait_for(predicate):
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Owned fixture condition exceeded configured wait budget")


def _pdf(path):
    import fitz

    path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 60), "Synthetic question preservation paper", fontsize=18)
        page.insert_text((50, 95), "Fixture Author", fontsize=12)
        page.insert_text((50, 130), "Methods", fontsize=15)
        page.insert_textbox(fitz.Rect(50, 160, 550, 700),
                            "This original synthetic source describes a local storage experiment. "
                            "The fixture contains one page and no clinical claims. "
                            "Question comments and manual notes are distinct records.\n" * 8)
        document.save(path)


def _seed(settings):
    database = build_zotero_db(settings.zotero_data_dir)
    parent = add_library_item(database, item_key=LIBRARY_KEY, title="Synthetic library paper",
                              abstract="Owned question acceptance fixture")
    pdf = settings.zotero_data_dir / "storage/ATTACH01/fixture.pdf"
    _pdf(pdf)
    with sqlite3.connect(database) as conn:
        attachment = conn.execute("INSERT INTO items(itemTypeID, libraryID, key) VALUES (3,1,'ATTACH01')").lastrowid
        conn.execute("INSERT INTO itemAttachments(itemID,parentItemID,linkMode,contentType,path) VALUES (?,?,1,?,?)",
                     (attachment, parent, "application/pdf", "storage:fixture.pdf"))
        unrelated = conn.execute("INSERT INTO items(itemTypeID,libraryID,key) VALUES (28,1,'MANUAL01')").lastrowid
        conn.execute("INSERT INTO itemNotes(itemID,parentItemID,note,title) VALUES (?,?,?,?)",
                     (unrelated, parent, '<div class="zotero-note znv1"><p>Unrelated native note</p></div>', "Manual"))
    stable = stable_feed_key_from_parts(guid="owned-question-browser-feed")
    item = {"guid": "owned-question-browser-feed", "stable_feed_key": stable, "source_type": "app",
            "title": "Synthetic Today paper", "url": FEED_URL, "abstract": "Owned app RSS fixture",
            "authors": "Fixture Author", "feed_name": "Synthetic acceptance feed"}
    with feeds.open_triage_conn(settings.triage_db_path) as conn:
        feed_id = rss.upsert_rss_feed(conn, name=item["feed_name"], url="https://synthetic.invalid/feed.xml")
        item_id, _ = rss.upsert_rss_item(conn, rss_feed_id=feed_id, item=item)
        feeds.record_decision(conn, run_id="synthetic-browser", feed_item={**item, "feed_library_id": feed_id,
                              "item_id": item_id}, decision=feeds.DECISION_AWAITING_REVIEW,
                              composite_score=4.0, reading_priority="should_read")
        conn.commit()
    _pdf(settings.pdf_cache_dir / f"{hashlib.sha256(FEED_URL.encode()).hexdigest()[:16]}.pdf")
    repositories.upsert_review_note(settings.triage_db_path, LIBRARY_KEY, "Initial manual note")
    return stable


class LocalServer:
    def __init__(self, settings, receipts):
        self.settings, self.receipts = settings, receipts
        self.socket = self._reserve(0)
        self.port = self.socket.getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.server = self.thread = None
        self.errors = []

    def _reserve(self, port):
        reserved = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            reserved.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            reserved.bind(("127.0.0.1", port))
        except OSError:
            reserved.close()
            raise
        return reserved

    def start(self):
        import uvicorn

        if self.socket is None:
            self.socket = self._reserve(self.port)
        app = api_app.create_app(self.settings)

        @app.middleware("http")
        async def record(request, call_next):
            body = await request.body()
            response = await call_next(request)
            self.receipts.append({"method": request.method, "path": request.url.path,
                                  "query": request.url.query,
                                  "body": json.loads(body) if body else None, "status": response.status_code})
            return response

        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port,
                                     log_level="warning", access_log=False, timeout_graceful_shutdown=WAIT_SECONDS))
        owned_socket = self.socket.dup()

        def run():
            try:
                self.server.run(sockets=[owned_socket])
            except BaseException as error:
                self.errors.append(repr(error))
            finally:
                owned_socket.close()
        self.thread = threading.Thread(target=run, name="owned-question-api", daemon=False)
        self.thread.start()
        wait_for(lambda: self.server.started or self.errors or not self.thread.is_alive())
        assert self.server.started and not self.errors, self.errors

    def stop(self):
        if self.thread is None:
            return
        self.server.should_exit = True
        self.thread.join(WAIT_SECONDS)
        assert not self.thread.is_alive(), "Owned API did not stop"
        assert not self.errors, self.errors
        self.thread = None
        self.socket.close()
        self.socket = None

    def close(self):
        try:
            self.stop()
        finally:
            if self.socket is not None:
                self.socket.close()
                self.socket = None


@dataclass
class BrowserLab:
    artifact: Path
    settings: Settings
    stable: str
    server: LocalServer
    playwright: object
    requests: list = field(default_factory=list)
    responses: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    page_errors: list = field(default_factory=list)
    cdp_sessions_closed: list = field(default_factory=list)
    context: object = None
    phase: str = "initialized"

    @property
    def database(self):
        return self.settings.triage_db_path

    def open_browser(self, offline=False):
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        assert chrome.is_file(), "Installed Chrome required; downloads forbidden"
        self.context = self.playwright.chromium.launch_persistent_context(
            str(self.artifact / "chrome-profile"), executable_path=str(chrome), headless=True,
            viewport={"width": 1440, "height": 1000}, service_workers="allow", offline=offline,
            args=["--disable-background-networking", "--disable-component-update", "--disable-sync",
                  "--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                  "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"],
        )
        self.context.set_offline(offline)  # Explicitly apply to reopened persistent targets before navigation.
        self.context.set_default_timeout(ACTION_TIMEOUT_MS)

        def restrict(route):
            if urlsplit(route.request.url).netloc != urlsplit(self.server.origin).netloc:
                self.blocked.append(route.request.url)
                route.abort()
            else:
                route.continue_()
        self.context.route("**/*", restrict)
        self.context.on("request", lambda request: self.requests.append(
            {"method": request.method, "url": request.url, "body": request.post_data}))

        def response(result):
            if "/api/" in result.url:
                self.responses.append({"url": result.url, "status": result.status, "body": result.json()})
        self.context.on("response", response)
        page = self.context.pages[0] if self.context.pages else self.context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        return page

    def close_browser(self):
        if self.context is not None:
            pages = self.context.pages
            self.context.close()
            self.cdp_sessions_closed.extend(page._question_main_world_closed for page in pages
                                            if hasattr(page, "_question_main_world_session"))
            self.context = None

    def checkpoint(self, phase, page=None):
        self.phase = phase
        write_json(self.artifact / "checkpoint.json", {"phase": phase, "origin": self.server.origin,
                   "fixture_root": str(self.settings.project_root), "pid": os.getpid(),
                   "integration_substitution": "Zotero connector probe suppressed; no API/QA/storage substitution"})
        write_json(self.artifact / "requests.json", self.server.receipts)
        write_json(self.artifact / "responses.json", self.responses)
        write_json(self.artifact / "browser-requests.json", self.requests)
        write_json(self.artifact / "database.json", {table: db_rows(self.database, f"SELECT * FROM {table} ORDER BY 1")
                                                   for table in TABLES})
        write_json(self.artifact / "zotero.json", db_rows(self.settings.zotero_data_dir / "zotero.sqlite",
                   "SELECT i.key,n.itemID,n.parentItemID,n.note,n.title FROM itemNotes n JOIN items i ON i.itemID=n.itemID"))
        if page is not None:
            write_json(self.artifact / f"indexeddb-{phase}.json", indexeddb(page))
            page.screenshot(path=str(self.artifact / f"{phase}.png"), full_page=True)


def synthetic_lab(monkeypatch):
    from patchright.sync_api import sync_playwright

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    artifact = Path(mkdtemp(prefix="run-", dir=EVIDENCE))
    root = artifact / "synthetic-project"
    root.mkdir()
    for section in ("CORPUS", "CLASSIFIER_GATE", "PRESTIGE", "FULL_TEXT_REFINE", "OPENREVIEW"):
        monkeypatch.setenv(f"ZS_{section}_ENABLED", "0")
    for name, value in {"ZS_OFFLINE": "1", "APP_LOG_FILE": "browser-smoke.log",
                        "PDF_ROOT": str(root), "ZOTERO_DATA_DIR": str(root / "data/synthetic-zotero"),
                        "ZOTERO_SUMMARIZER_HOME": str(root), "NO_PROXY": "*"}.items():
        monkeypatch.setenv(name, value)
    settings = Settings.load(project_root=root)
    bootstrap.bootstrap_phase0(settings)
    config = read_config(settings.config_path)
    config.llm_enabled = False
    config.research_goals = ["Synthetic storage preservation"]
    write_user_config(settings.config_path, config)
    monkeypatch.setenv("ZS_QUALITY_REVIEW_USE_DOCLING", "0")
    monkeypatch.setattr(lifecycle, "startup", partial(lifecycle.startup, background=False))
    monkeypatch.setattr(ZoteroWriter, "is_connector_running", lambda _: False)
    stable = _seed(settings)
    server = LocalServer(settings, [])

    def allowed_connect(original, sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            assert sock.family == socket.AF_INET and address == ("127.0.0.1", server.port), address
        return original(sock, address)
    monkeypatch.setattr(socket.socket, "connect", lambda sock, address: allowed_connect(_CONNECT, sock, address))
    monkeypatch.setattr(socket.socket, "connect_ex", lambda sock, address: allowed_connect(_CONNECT_EX, sock, address))
    with sync_playwright() as playwright:
        lab = BrowserLab(artifact, settings, stable, server, playwright)
        try:
            server.start()
            assert get_context().state.app_state.config.quality_review.use_docling is False
            paper_render.build_paper_read(LIBRARY_KEY)
            paper_render.build_paper_read(stable, allow_acquire_missing=True)
            write_json(artifact / "provenance.json", {"root": str(ROOT), "settings": vars(settings),
                       "imports": {"api_app": api_app.__file__, "lifecycle": lifecycle.__file__,
                                   "paper_render": paper_render.__file__, "repositories": repositories.__file__},
                       "source_hashes": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in [Path(api_app.__file__), Path(paper_render.__file__),
                                         ROOT / "tests/test_review_question_browser.py", Path(__file__)]},
                       "pdf_hashes": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                                      for path in root.rglob("*.pdf")}})
            lab.checkpoint("ready")
            yield lab
        finally:
            lab.checkpoint(lab.phase)
            lab.close_browser()
            server.close()
            write_json(artifact / "cleanup.json", {"server_thread_absent": server.thread is None,
                       "browser_context_closed": lab.context is None, "main_world_sessions_closed": lab.cdp_sessions_closed,
                       "page_errors": lab.page_errors, "blocked_foreign_requests": lab.blocked})
        assert all(lab.cdp_sessions_closed), "Owned CDP session not closed with context"
        assert lab.page_errors == [], lab.page_errors
        assert lab.blocked == [], lab.blocked
