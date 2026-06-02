"""Unit tests for the slop gate (Tier 7), loaded by path like the sibling gates.

Every rule is exercised with a SLOP case (must fire) and a legitimate NEAR-MISS
(must NOT fire) — the pairs that prove each guard encodes a shape, not a snippet.
The near-misses are the real in-tree idioms the adversarial review surfaced:
the intentional-ignore binding, the context-carrying log, the shadowed builtin,
the read-only set default, tracker-linked debt, why-comments, ML/`temp` names,
flat data-shuttle signatures.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

_PRECOMMIT = Path(__file__).resolve().parent.parent / "tools" / "precommit"
sys.path.insert(0, str(_PRECOMMIT))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _PRECOMMIT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


text = _load("_slop_text")
slop = _load("check_slop")

# build the work-marker token without writing it literally (self-detection avoidance)
_TODO = "TO" + "DO"


def _rules(source: str) -> list[tuple[str, str]]:
    diags = slop.find_slop(textwrap.dedent(source), "zotero_summarizer/p.py")
    return sorted({(d.rule, d.severity) for d in diags})


_SLOP_CASES = [
    ("try:\n    risky()\nexcept Exception:\n    pass\n", [("slop/swallowed-exception", "advise")]),
    ("import logging\nL = logging.getLogger(__name__)\ntry:\n    f()\nexcept Exception as e:\n    L.error('failed')\n", [("slop/silent-recovery", "advise")]),
    ("def parse(r):\n    breakpoint()\n    return r\n", [("slop/debug-leftover", "block")]),
    ("import pdb\ndef parse(r):\n    pdb.set_trace()\n    return r\n", [("slop/debug-leftover", "block")]),
    ("def collect(items, seen=[]):\n    seen.append(items)\n    return seen\n", [("slop/mutable-default", "advise")]),
    ("from collections import Counter\ndef tally(evs, counts=Counter()):\n    for e in evs:\n        counts[e] += 1\n    return counts\n", [("slop/mutable-default", "advise")]),
    (f"# {_TODO} fix the ranking before launch\nx = 1\n", [("slop/todo-stub", "advise")]),
    ("def helper_2(d):\n    return d\n", [("slop/generic-naming", "advise")]),
    ("# Return the result\nval = compute()\n", [("slop/comment-slop", "advise")]),
]

_NEAR_MISS = [
    "try:\n    risky()\nexcept Exception as _:\n    pass\n",                       # intentional-ignore binding
    "try:\n    risky()\nexcept FileNotFoundError:\n    raise ConfigError('x') from None\n",  # re-raise
    "try:\n    f()\nexcept Exception as e:\n    L.error('failed: %s', e)\n",       # context carried
    "try:\n    f()\nexcept Exception:\n    L.warning('x', exc_info=True)\n",        # exc_info
    "def breakpoint(series):\n    return series\n",                                # shadowed builtin (domain fn)
    "class T:\n    def set_trace(self):\n        self.on = True\n",                # non-debugger receiver
    "def collect(items, seen=None):\n    if seen is None:\n        seen = []\n    return seen\n",  # None default
    "def is_blocked(ct, restricted={'a', 'b'}):\n    return ct in restricted\n",   # read-only set default
    f"# {_TODO}(gh-412): fix the ranking\nx = 1\n",                                # tracker-linked debt
    "# a temp dir is used here for safety\nx = 1\n",                               # 'temp' mid-sentence noun
    "def sha256_digest(payload):\n    return payload\n",                          # digit not a generic stem
    "# Return early because the cache is cold and a recompute would stall the request\nx = 1\n",  # why + long
]


@pytest.mark.parametrize("source,expected", _SLOP_CASES)
def test_slop_fires(source: str, expected: list[tuple[str, str]]) -> None:
    assert _rules(source) == expected


@pytest.mark.parametrize("source", _NEAR_MISS)
def test_near_miss_spared(source: str) -> None:
    assert _rules(source) == []


# --- pure helpers ---


def _func(source: str):
    return ast.parse(textwrap.dedent(source)).body[0]


@pytest.mark.parametrize(
    "name,expected",
    [("foo", True), ("bar", True), ("helper_2", True), ("util3", True), ("handler_1", True),
     ("sha256_digest", False), ("payload_v2", False), ("__init__", False), ("temperature", False)],
)
def test_is_generic_name(name: str, expected: bool) -> None:
    assert slop.is_generic_name(name) is expected


def test_module_shadowed_names_excludes_imports() -> None:
    # an import must NOT count as a shadow (else `import pdb` would hide pdb.set_trace).
    names = slop.module_shadowed_names(ast.parse("import pdb\nfrom collections import Counter\ndef f(x):\n    y = 1\n    return y\n"))
    assert {"f", "x", "y"} <= names
    assert "pdb" not in names and "Counter" not in names


def test_required_params_excludes_self_and_defaults() -> None:
    fn = _func("def m(self, a, b, c=1, *args, d, e=2, **kw):\n    return a\n")
    assert slop.required_params(fn) == 3  # a, b, d (self/c/e/args/kw excluded)


def test_nesting_depth_counts_elif_chain_as_one_level() -> None:
    flat = _func("def f(x):\n    if x == 1:\n        return 1\n    elif x == 2:\n        return 2\n    elif x == 3:\n        return 3\n    return 0\n")
    nested = _func("def f(x):\n    if x:\n        for i in x:\n            while i:\n                return i\n")
    assert slop.nesting_depth(flat.body) == 1   # elif chain is one level, not three
    assert slop.nesting_depth(nested.body) == 3


def test_param_mutated_detects_real_mutation() -> None:
    assert slop.param_mutated(_func("def f(s):\n    s.append(1)\n"), "s") is True
    assert slop.param_mutated(_func("def f(s):\n    return 1 in s\n"), "s") is False


def test_load_severity_overrides_rejects_bad_value() -> None:
    assert slop.load_severity_overrides("slop/comment-slop=off\n") == {"slop/comment-slop": "off"}
    with pytest.raises(ValueError):
        slop.load_severity_overrides("slop/comment-slop=loud\n")


def test_diagnostic_key_format() -> None:
    diag = slop.Diagnostic("p.py", 7, 0, "slop/todo-stub", "advise", "comments", "m")
    assert diag.key == "p.py:7:slop/todo-stub"


def test_find_slop_propagates_syntax_error() -> None:
    with pytest.raises(SyntaxError):
        slop.find_slop("def broken(:\n", "p.py")


def test_scan_todos_ignores_marker_inside_string() -> None:
    # the marker lives in a STRING, not a comment -> tokenize separates them -> not flagged.
    assert text.scan_todos(f'msg = "{_TODO}: ship it"\n') == []
