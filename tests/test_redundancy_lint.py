"""Unit tests for the redundancy gate's pure functions (Tier 6).

``tools/precommit/`` is not an importable package, so both modules are loaded by
path (as in ``test_dead_code_guard.py``). Only pure helpers — AST/string, no git, no
filesystem — are exercised; the git-backed subcommands are covered by the live
``pre-commit`` runs during verification.

The emphasis is SOUNDNESS: the conditionally-unsound transforms the adversarial review
surfaced (``dict(list(x))``, ``set(sorted(x))``, ``abs(abs(x))``, shadowed builtins,
lazy genexp/map) must be ADVISE or suppressed, never BLOCK.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

_PRECOMMIT = Path(__file__).resolve().parent.parent / "tools" / "precommit"
sys.path.insert(0, str(_PRECOMMIT))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _PRECOMMIT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # register before exec (future-annotations dataclass resolution)
    spec.loader.exec_module(module)
    return module


clones = _load("_redundancy_clones")
red = _load("check_redundancy")


def _kinds(source: str) -> list[tuple[str, str]]:
    return [(f.kind, f.severity) for f in red.find_transforms(source, "p.py")]


def _func(source: str) -> ast.AST:
    return ast.parse(source).body[0]


# --- transform detection (parametrized over snippet -> expected (kind, severity)) ---

_IDEMPOTENT_BLOCK = [
    "sorted(sorted(x))",
    "set(set(x))",
    "frozenset(frozenset(x))",
    "list(list(x))",
    "tuple(tuple(x))",
    "dict(dict(x))",
    "str(str(x))",
    "int(int(x))",
    "float(float(x))",
    "bool(bool(x))",
]
_IDEMPOTENT_ADVISE = ["abs(abs(x))", "round(round(x))"]
_IDEMPOTENT_NONE = [
    "sorted(x)",
    "abs(x)",
    "len(len(x))",
    "sorted(sorted(rows, key=a), key=b)",  # two-key stable sort — keywords block the guard
    "round(round(x, 2), 4)",               # ndigits second positional
    "round(round(x), 2)",                  # outer not simple
    "np.sort(np.sort(x))",                 # attribute, not a bare builtin
    "obj.f(obj.f(x))",
]
_ROUNDTRIP_BLOCK = ["sorted(list(x))", "sorted(tuple(x))", "list(tuple(x))", "tuple(list(x))"]
_ROUNDTRIP_ADVISE = [
    "dict(list(x))",
    "dict(tuple(x))",
    "set(list(x))",
    "set(tuple(x))",
    "frozenset(list(x))",
    "frozenset(tuple(x))",
    "set(sorted(x))",
    "frozenset(sorted(x))",
]
_ROUNDTRIP_NONE = [
    "list(set(x))",      # set drops multiplicity — not faithful
    "list(sorted(x))",   # order-sensitive outer over reordered inner
    "dict(sorted(x))",
    "tuple(sorted(x))",
    "sorted(set(x))",    # set before sort changes content
    "dict(zip(a, b))",   # zip not a curated inner
    "set(map(f, x))",    # map inner is not simple (2 args)
]
_COMP_BLOCK = ["[x for x in it]", "{x for x in it}", "{k: v for k, v in d.items()}"]
_COMP_MAP_ADVISE = ["(x for x in it)", "map(lambda x: x, it)"]
_COMP_MAP_NONE = [
    "[f(x) for x in it]",
    "[x for x in it if x]",
    "[y for x in it]",
    "[x for x in a for y in b]",
    "{v: k for k, v in d.items()}",
    "{k: g(v) for k, v in d.items()}",
    "{k: v for k, v in pairs}",            # non-.items() source — legit materialization
    "[x.strip() for x in it]",
    "map(str, it)",
    "map(lambda x: x + 1, it)",
    "map(lambda x, y: x, p)",
    "map(lambda x=1: x, it)",
    "obj.map(lambda x: x, it)",
]
_INVOLUTION_BLOCK = ["y = -(-x)", "y = ~~x", "y = - -x"]
_INVOLUTION_NONE = ["y = -x", "y = ~x", "y = not not x", "y = +(+x)", "y = ~-x", "y = -~x"]
_SHADOWED_NONE = [
    "def sorted(z):\n    return z\nout = sorted(sorted(rows))",
    "import collections as dict\nd = dict(dict(p))",
    "def f(set):\n    return set(set(x))",
    "list = make()\ny = list(list(z))",
]


@pytest.mark.parametrize("src", _IDEMPOTENT_BLOCK)
def test_idempotent_block(src: str) -> None:
    assert _kinds(src) == [("idempotent", "block")]


@pytest.mark.parametrize("src", _IDEMPOTENT_ADVISE)
def test_idempotent_advise(src: str) -> None:
    assert _kinds(src) == [("idempotent", "advise")]


@pytest.mark.parametrize("src", _ROUNDTRIP_BLOCK)
def test_roundtrip_block(src: str) -> None:
    assert _kinds(src) == [("round-trip", "block")]


@pytest.mark.parametrize("src", _ROUNDTRIP_ADVISE)
def test_roundtrip_advise(src: str) -> None:
    assert _kinds(src) == [("round-trip", "advise")]


@pytest.mark.parametrize("src", _COMP_BLOCK)
def test_comprehension_block(src: str) -> None:
    assert _kinds(src) == [("identity-comp", "block")]


@pytest.mark.parametrize("src", _COMP_MAP_ADVISE)
def test_comprehension_map_advise(src: str) -> None:
    [(kind, severity)] = _kinds(src)
    assert kind in {"identity-comp", "identity-map"} and severity == "advise"


@pytest.mark.parametrize("src", _INVOLUTION_BLOCK)
def test_involution_block(src: str) -> None:
    assert _kinds(src) == [("involution", "block")]


@pytest.mark.parametrize(
    "src",
    _IDEMPOTENT_NONE
    + _ROUNDTRIP_NONE
    + _COMP_MAP_NONE
    + _INVOLUTION_NONE
    + _SHADOWED_NONE,
)
def test_not_flagged(src: str) -> None:
    assert _kinds(src) == []


# --- pure call-shape helpers ---


@pytest.mark.parametrize(
    "src,expected",
    [
        ("f(x)", True),
        ("f(x, key=a)", False),
        ("f(*x)", False),
        ("f(x, y)", False),
        ("f(**k)", False),
        ("f()", False),
    ],
)
def test_is_simple_call(src: str, expected: bool) -> None:
    call = ast.parse(src).body[0].value
    assert red.is_simple_call(call) is expected


def test_called_name_bare_name() -> None:
    assert red.called_name(ast.parse("sorted(x)").body[0].value) == "sorted"


def test_called_name_attribute_is_none() -> None:
    assert red.called_name(ast.parse("np.sort(x)").body[0].value) is None


@pytest.mark.parametrize(
    "outer,inner,expected",
    [
        ("list", "tuple", "block"),
        ("sorted", "list", "block"),
        ("dict", "list", "advise"),
        ("set", "list", "advise"),
        ("set", "sorted", "advise"),
        ("frozenset", "sorted", "advise"),
        ("list", "set", None),
        ("list", "sorted", None),
        ("sorted", "set", None),
    ],
)
def test_roundtrip_severity(outer: str, inner: str, expected) -> None:
    assert red.roundtrip_severity(outer, inner) == expected


def test_bound_names_collects_defs_imports_params() -> None:
    src = "import os\nfrom a import b as c\ndef fn(p):\n    q = 1\n    return p\n"
    names = red.bound_names(ast.parse(src))
    assert {"os", "c", "fn", "p", "q"} <= names


# --- the dead-constant regression: IDEMPOTENT_BLOCK must be the source of truth ---


def test_abs_round_excluded_from_block_set() -> None:
    assert "abs" not in red.IDEMPOTENT_BLOCK and "round" not in red.IDEMPOTENT_BLOCK
    assert {"abs", "round"} == set(red.IDEMPOTENT_ADVISE)


def test_block_set_drives_flagging() -> None:
    # Proves the constant is consulted on the live path (not dead): every member flags.
    for name in red.IDEMPOTENT_BLOCK:
        assert _kinds(f"{name}({name}(x))") == [("idempotent", "block")]


# --- allowlist + Finding key + error propagation ---


def test_finding_key_format() -> None:
    finding = red.Finding(path="p.py", line=3, kind="idempotent", severity="block")
    assert finding.key == "p.py:3:idempotent"


def test_load_allowlist_strips_comments_and_blanks() -> None:
    text = "# header\n\np.py:3:idempotent  # frozen\n  a.py:b::c.py:d \n# x:y:z\n"
    assert red.load_allowlist(text) == {"p.py:3:idempotent", "a.py:b::c.py:d"}


def test_find_transforms_propagates_syntax_error() -> None:
    with pytest.raises(SyntaxError):
        red.find_transforms("def broken(:\n", "p.py")


# --- clone machinery (pure) ---


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (frozenset("ab"), frozenset("ab"), 1.0),
        (frozenset("ab"), frozenset("cd"), 0.0),
        (frozenset(), frozenset(), 1.0),
        (frozenset("a"), frozenset(), 0.0),
        (frozenset("abc"), frozenset("abcd"), 0.75),
    ],
)
def test_jaccard(a, b, expected) -> None:
    assert clones.jaccard(a, b) == pytest.approx(expected)


@pytest.mark.parametrize(
    "sa,sb,thr,expected",
    [
        (8, 8, 0.7, True),
        (10, 8, 0.7, True),
        (10, 3, 0.7, False),  # bound 0.3 < 0.7 -> skipped before scoring
        (20, 4, 0.5, False),
        (0, 0, 0.7, True),
    ],
)
def test_length_bound_ok(sa, sb, thr, expected) -> None:
    assert clones.length_bound_ok(sa, sb, thr) is expected


_FN_A = "def a(x):\n    t = foo(x)\n    return t + bar(x) + baz(x)\n"
_FN_B = "def b(y):\n    u = foo(y)\n    return u + bar(y) + baz(y)\n"   # renamed locals, same APIs
_FN_C = "def c(x):\n    t = QUX(x)\n    return t + WIB(x) + ZAP(x)\n"   # same shape, disjoint APIs


def test_renamed_locals_share_skeleton_and_tokens() -> None:
    fa = clones.fingerprint_function(_func(_FN_A), "p.py", "a")
    fb = clones.fingerprint_function(_func(_FN_B), "p.py", "b")
    assert fa.skeleton == fb.skeleton
    assert clones.jaccard(fa.free_tokens, fb.free_tokens) == 1.0


def test_disjoint_apis_share_skeleton_but_not_tokens() -> None:
    fa = clones.fingerprint_function(_func(_FN_A), "p.py", "a")
    fc = clones.fingerprint_function(_func(_FN_C), "p.py", "c")
    assert fa.skeleton == fc.skeleton  # structure matches...
    assert clones.jaccard(fa.free_tokens, fc.free_tokens) == 0.0  # ...but vocabulary is disjoint


def test_semantic_tokens_keep_free_and_attrs_drop_locals() -> None:
    fn = _func("def m(self):\n    a = self.repo.load(key)\n    return helper(a)\n")
    locals_ = clones._local_names(fn)
    tokens = clones.semantic_tokens(fn, locals_)
    assert {"repo", "load", "key", "helper"} <= tokens
    assert "a" not in tokens and "self" not in tokens  # self/cls + assigned locals excluded


def test_find_clone_pairs_reports_genuine_drops_disjoint() -> None:
    records = [clones.fingerprint_function(_func(s), "p.py", n) for s, n in
               [(_FN_A, "a"), (_FN_B, "b"), (_FN_C, "c")]]
    pairs = clones.find_clone_pairs(records, 0.75, set())
    assert [(p.a.qualname, p.b.qualname) for p in pairs] == [("a", "b")]
    assert pairs[0].score == 1.0


def test_find_clone_pairs_allowlist_suppresses() -> None:
    records = [clones.fingerprint_function(_func(s), "p.py", n) for s, n in
               [(_FN_A, "a"), (_FN_B, "b")]]
    pairs = clones.find_clone_pairs(records, 0.75, set())
    assert clones.find_clone_pairs(records, 0.75, {pairs[0].key}) == []


def test_clone_pair_key_order_independent() -> None:
    fa = clones.fingerprint_function(_func(_FN_A), "z.py", "a")
    fb = clones.fingerprint_function(_func(_FN_B), "a.py", "b")
    assert clones.ClonePair(fa, fb, 1.0).key == clones.ClonePair(fb, fa, 1.0).key


def test_node_count_floor_drops_trivial() -> None:
    assert clones.collect_fingerprints_from_source("def g(x):\n    return x\n", "p.py") == []


def test_below_threshold_not_reported() -> None:
    # Same skeleton, partial token overlap below 0.75.
    src_a = "def a(x):\n    return foo(x) + bar(x) + baz(x) + qux(x)\n"
    src_b = "def b(y):\n    return foo(y) + bar(y) + zip(y) + zap(y)\n"
    fa = clones.fingerprint_function(_func(src_a), "p.py", "a")
    fb = clones.fingerprint_function(_func(src_b), "p.py", "b")
    assert fa.skeleton == fb.skeleton
    assert clones.find_clone_pairs([fa, fb], 0.75, set()) == []
