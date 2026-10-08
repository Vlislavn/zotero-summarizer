"""Bounded extraction of article DOM text for controlled text-only PDF output."""
from __future__ import annotations

import math
import os
import time
import uuid
from typing import Any

_NODE_BUDGET_ENV = "ZS_BROWSER_ARTICLE_NODE_BUDGET"
_DEFAULT_DOM_NODE_BUDGET = 100_000
_MAX_DOM_NODE_BUDGET = 1_000_000
_TEXT_CHUNK_BYTES = 8_192
_ISOLATED_WORLD = "zs-article-text"

_READER_EXPRESSION = r"""(() => {
  const key = "__STATE__";
  const byteLimit = __BYTE_LIMIT__;
  const nodeBudget = __NODE_BUDGET__;
  const chunkLimit = __CHUNK_LIMIT__;
  const root = document.body;
  const initialDocument = document;
  const initialUrl = location.href;
  const walker = root && document.createTreeWalker(root, NodeFilter.SHOW_ALL);
  const observer = new MutationObserver(() => { changed = true; });
  let changed = false;
  if (root) observer.observe(document, {subtree: true, childList: true, characterData: true, attributes: true});
  let current = null;
  let offset = 0;
  let nodes = root ? 1 : 0;
  let textNodes = 0;
  let emitted = 0;
  let separator = false;
  let finished = false;

  function result(text, status, done) { return {text, status, done, nodes}; }
  function ignored(node) {
    const parent = node.parentElement;
    return Boolean(parent && parent.closest("script,style,noscript,template"));
  }
  function cleanup() {
    observer.disconnect();
    delete globalThis[key];
  }
  function step() {
    if (document !== initialDocument || location.href !== initialUrl) return result("", "mutated", false);
    if (observer.takeRecords().length) changed = true;
    if (changed) return result("", "mutated", false);
    if (!walker) return result("", "empty", true);
    if (finished) return result("", "complete", true);
    let text = "";
    let chunkBytes = 0;
    while (true) {
      if (!current || offset >= current.nodeValue.length) {
        current = null;
        while (!current) {
          const node = walker.nextNode();
          if (!node) {
            finished = true;
            return result(text, "complete", true);
          }
          nodes += 1;
          if (nodes > nodeBudget) return result(text, "incomplete", false);
          if (node.nodeType !== Node.TEXT_NODE || ignored(node)) continue;
          const raw = node.nodeValue || "";
          if (!raw) continue;
          current = node;
          offset = 0;
          separator = textNodes > 0;
          textNodes += 1;
        }
      }
      if (separator) {
        if (emitted + 1 > byteLimit) return result(text, "overbudget", false);
        if (chunkBytes + 1 > chunkLimit) return result(text, "more", false);
        text += "\n";
        emitted += 1;
        chunkBytes += 1;
        separator = false;
      }
      const raw = current.nodeValue || "";
      if (offset >= raw.length) continue;
      const point = raw.codePointAt(offset);
      if (point >= 0xd800 && point <= 0xdfff) return result(text, "invalid_unicode", false);
      const width = point > 0xffff ? 2 : 1;
      const value = raw.slice(offset, offset + width);
      const size = point <= 0x7f ? 1 : point <= 0x7ff ? 2 : point <= 0xffff ? 3 : 4;
      if (emitted + size > byteLimit) return result(text, "overbudget", false);
      if (chunkBytes + size > chunkLimit) return result(text, "more", false);
      text += value;
      emitted += size;
      chunkBytes += size;
      offset += width;
      if (chunkBytes >= chunkLimit) return result(text, "more", false);
    }
  }
  const reader = {step, cleanup};
  globalThis[key] = reader;
  return reader.step();
})()"""


def article_text_limit(max_bytes: int) -> int:
    """Use the frozen text-PDF writer's budget for its bounded source text."""
    from zotero_summarizer.integrations._article_pdf import article_text_limit as limit_text

    return limit_text(max_bytes)


def render_text_pdf(text: str, *, max_bytes: int) -> bytes:
    """Delegate text-only PDF creation to the frozen writer interface."""
    from zotero_summarizer.integrations._article_pdf import render_text_pdf as render_text

    return render_text(text, max_bytes=max_bytes)


def _configured_node_budget() -> int:
    raw = os.environ.get(_NODE_BUDGET_ENV)
    if raw is None:
        return _DEFAULT_DOM_NODE_BUDGET
    if not raw.isascii() or not raw.isdecimal():
        raise ValueError(f"{_NODE_BUDGET_ENV} must be a positive decimal integer")
    budget = int(raw)
    if not 0 < budget <= _MAX_DOM_NODE_BUDGET:
        raise ValueError(f"{_NODE_BUDGET_ENV} must be between 1 and {_MAX_DOM_NODE_BUDGET} (node budget)")
    return budget


def validate_article_timeout(timeout: float) -> float:
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    return float(timeout)


def _remaining_timeout_ms(deadline: float) -> int:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("article DOM text extraction timed out")
    return max(1, int(remaining * 1000))


def _execution_context(cdp: Any, collection_id: str) -> int:
    tree = cdp.send("Page.getFrameTree").get("frameTree", {})
    frame = tree.get("frame", {})
    frame_id = frame.get("id")
    if not frame_id:
        raise ValueError("article page has no main frame")
    world = cdp.send("Page.createIsolatedWorld", {
        "frameId": frame_id, "worldName": f"{_ISOLATED_WORLD}-{collection_id}",
    })
    context_id = world.get("executionContextId")
    if type(context_id) is not int:
        raise ValueError("browser did not create an isolated DOM world")
    return context_id


def _expression(first: bool, max_bytes: int, node_budget: int, state_name: str) -> str:
    if not first:
        return f"globalThis[{state_name!r}].step()"
    return (
        _READER_EXPRESSION.replace("__STATE__", state_name)
        .replace("__BYTE_LIMIT__", str(max_bytes))
        .replace("__NODE_BUDGET__", str(node_budget))
        .replace("__CHUNK_LIMIT__", str(_TEXT_CHUNK_BYTES))
    )


def _evaluate_chunk(cdp: Any, context_id: int, expression: str, deadline: float) -> dict[str, Any]:
    result = cdp.send("Runtime.evaluate", {
        "expression": expression,
        "contextId": context_id,
        "returnByValue": True,
        "awaitPromise": False,
        "timeout": _remaining_timeout_ms(deadline),
    })
    if result.get("exceptionDetails"):
        raise RuntimeError("isolated article DOM text extraction failed")
    remote = result.get("result", {})
    value = remote.get("value") if isinstance(remote, dict) else None
    if not isinstance(value, dict):
        raise ValueError("article DOM text extraction returned an incomplete chunk")
    return value


def _validate_chunk(item: dict[str, Any], node_budget: int, previous_nodes: int) -> tuple[str, str, int]:
    status = item.get("status")
    if not isinstance(status, str) or status not in {
        "more", "complete", "empty", "overbudget", "incomplete", "mutated", "invalid_unicode",
    }:
        raise ValueError("article DOM text extraction returned an invalid status")
    done = item.get("done")
    if type(done) is not bool:
        raise ValueError("article DOM text extraction returned an invalid done flag")
    nodes = item.get("nodes")
    if type(nodes) is not int or nodes < previous_nodes:
        raise ValueError("article DOM text extraction returned invalid nodes accounting")
    if nodes > node_budget:
        raise ValueError("article DOM walk exceeded its node budget")
    if done != (status in {"complete", "empty"}):
        raise ValueError("article DOM text extraction returned an inconsistent done flag")
    if status == "overbudget":
        raise ValueError("article text exceeds max_bytes")
    if status == "incomplete":
        raise ValueError("article DOM walk exceeded its node budget")
    if status == "mutated":
        raise ValueError("article DOM changed during text extraction")
    if status == "invalid_unicode":
        raise ValueError("article text contains invalid Unicode")
    text = item.get("text")
    if not isinstance(text, str):
        raise ValueError("article DOM text extraction returned non-text data")
    try:
        chunk_bytes = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("article text contains invalid Unicode") from exc
    if chunk_bytes > _TEXT_CHUNK_BYTES:
        raise ValueError("article DOM text extraction returned an oversized chunk")
    return status, text, nodes


def _cleanup_reader(cdp: Any, context_id: int, state_name: str) -> None:
    result = cdp.send("Runtime.evaluate", {
        "expression": f"globalThis[{state_name!r}] && globalThis[{state_name!r}].cleanup()",
        "contextId": context_id,
        "returnByValue": True,
        "awaitPromise": False,
    })
    if result.get("exceptionDetails"):
        raise RuntimeError("isolated article DOM reader cleanup failed")


def collect_article_text(page: Any, *, max_bytes: int, timeout: float) -> str:
    """Read all article text in capped chunks from a trusted CDP isolated world."""
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    timeout = validate_article_timeout(timeout)
    node_budget = _configured_node_budget()
    deadline = time.monotonic() + timeout
    cdp = page.context.new_cdp_session(page)
    context_id = None
    state_name = f"__zsArticleTextReader_{uuid.uuid4().hex}"
    primary_error: BaseException | None = None
    try:
        context_id = _execution_context(cdp, state_name.rsplit("_", 1)[-1])
        pieces: list[str] = []
        total_bytes = 0
        previous_nodes = 0
        first = True
        while True:
            item = _evaluate_chunk(
                cdp, context_id, _expression(first, max_bytes, node_budget, state_name), deadline,
            )
            first = False
            status, chunk, previous_nodes = _validate_chunk(item, node_budget, previous_nodes)
            try:
                chunk_bytes = len(chunk.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise ValueError("article text contains invalid Unicode") from exc
            if total_bytes + chunk_bytes > max_bytes:
                raise ValueError("article text exceeds max_bytes")
            total_bytes += chunk_bytes
            if chunk:
                pieces.append(chunk)
            if status in {"complete", "empty"}:
                break
            if not chunk:
                raise ValueError("article DOM text extraction made no progress")

        text = "".join(pieces)
        if not text.strip():
            raise ValueError("article page contains no extractable text")
        return text
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            if context_id is not None:
                _cleanup_reader(cdp, context_id, state_name)
        except Exception as cleanup_error:
            if primary_error is None:
                raise
            raise primary_error from cleanup_error
        finally:
            cdp.detach()
