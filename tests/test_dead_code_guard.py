"""Unit tests for the dead-code guard's pure functions.

``tools/precommit/`` is not an importable package, so the module is loaded by
path. Only the pure (no git / no vulture / no filesystem) helpers are exercised
here; the git- and vulture-backed paths are covered by the live ``pre-commit``
runs during verification.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

_GUARD_PATH = (
    Path(__file__).resolve().parent.parent / "tools" / "precommit" / "check_dead_code.py"
)
_spec = importlib.util.spec_from_file_location("check_dead_code", _GUARD_PATH)
assert _spec is not None and _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
# Register before exec: ``from __future__ import annotations`` makes the dataclass
# field annotations strings, which dataclasses resolves via ``sys.modules``.
sys.modules["check_dead_code"] = guard
_spec.loader.exec_module(guard)


# --- public_symbol_definitions ---


def test_public_symbol_definitions_keeps_public_top_level() -> None:
    source = "def alpha():\n    pass\n\nasync def beta():\n    pass\n\nclass Gamma:\n    pass\n"
    defs = guard.public_symbol_definitions(source, "pkg/mod.py")
    assert [(d.name, d.line) for d in defs] == [("alpha", 1), ("beta", 4), ("Gamma", 7)]
    assert all(d.path == "pkg/mod.py" for d in defs)


def test_public_symbol_definitions_skips_private() -> None:
    source = "def _hidden():\n    pass\n\nclass _Secret:\n    pass\n"
    assert guard.public_symbol_definitions(source, "pkg/mod.py") == []


def test_public_symbol_definitions_skips_decorated() -> None:
    source = (
        "import mcp\n"
        "@mcp.tool()\n"
        "def registered():\n"
        "    pass\n"
        "@router.get('/x')\n"
        "async def handler():\n"
        "    pass\n"
    )
    assert guard.public_symbol_definitions(source, "pkg/mod.py") == []


def test_public_symbol_definitions_ignores_nested() -> None:
    source = "def outer():\n    def inner():\n        pass\n    return inner\n"
    defs = guard.public_symbol_definitions(source, "pkg/mod.py")
    assert [d.name for d in defs] == ["outer"]


def test_public_symbol_definitions_propagates_syntax_error() -> None:
    with pytest.raises(SyntaxError):
        guard.public_symbol_definitions("def broken(:\n", "pkg/mod.py")


# --- symbols_without_consumers ---


def _sym(name: str, line: int) -> object:
    return guard.PublicSymbol(path="pkg/mod.py", line=line, name=name)


def test_self_reference_only_is_orphan() -> None:
    definition = _sym("alpha", 10)
    refs = {"alpha": [("pkg/mod.py", 10)]}  # only its own def line
    assert guard.symbols_without_consumers([definition], refs) == [definition]


def test_no_reference_is_orphan() -> None:
    definition = _sym("alpha", 10)
    assert guard.symbols_without_consumers([definition], {}) == [definition]


def test_cross_file_reference_is_consumed() -> None:
    definition = _sym("alpha", 10)
    refs = {"alpha": [("pkg/mod.py", 10), ("pkg/other.py", 3)]}
    assert guard.symbols_without_consumers([definition], refs) == []


def test_same_file_other_line_is_consumed() -> None:
    # e.g. router.add_api_route("/p", alpha, ...) or __all__ = ["alpha"] later in the file.
    definition = _sym("alpha", 10)
    refs = {"alpha": [("pkg/mod.py", 10), ("pkg/mod.py", 99)]}
    assert guard.symbols_without_consumers([definition], refs) == []


# --- load_consumer_allowlist ---


def test_load_consumer_allowlist_parses_and_strips() -> None:
    text = (
        "# header comment\n"
        "\n"
        "pkg/a.py:foo   # test-only\n"
        "  pkg/b.py:Bar  \n"
        "# pkg/c.py:skipped\n"
    )
    assert guard.load_consumer_allowlist(text) == {"pkg/a.py:foo", "pkg/b.py:Bar"}


def test_public_symbol_key_matches_allowlist_format() -> None:
    assert _sym("foo", 1).key == "pkg/mod.py:foo"


# --- parse_vulture_findings ---


def test_parse_vulture_findings_extracts_fields() -> None:
    output = (
        "zotero_summarizer/x.py:12: unused import 'os' (90% confidence)\n"
        "not a finding line\n"
        "zotero_summarizer/y.py:5: unreachable code after 'return' (100% confidence)\n"
    )
    findings = guard.parse_vulture_findings(output)
    assert [(f.path, f.line, f.confidence) for f in findings] == [
        ("zotero_summarizer/x.py", 12, 90),
        ("zotero_summarizer/y.py", 5, 100),
    ]


def test_split_blocking_advisory_partitions_by_confidence() -> None:
    findings = [
        guard.VultureFinding("a.py", 1, "m", 60),
        guard.VultureFinding("b.py", 2, "m", 79),
        guard.VultureFinding("c.py", 3, "m", 80),
        guard.VultureFinding("d.py", 4, "m", 100),
    ]
    blocking, advisory = guard.split_blocking_advisory(findings, 80)
    assert [f.confidence for f in blocking] == [80, 100]
    assert [f.confidence for f in advisory] == [60, 79]


# --- R1: vulture_argv is the single ignore-policy seam ---


def test_vulture_argv_is_the_single_ignore_policy_seam() -> None:
    # The Makefile + README route through this; the ignore policy is sourced from
    # the constants in ONE place, so testing the seam (not Makefile text) is enough.
    argv = guard.vulture_argv(60)
    assert "--ignore-decorators" in argv and guard.VULTURE_IGNORE_DECORATORS in argv
    assert "--ignore-names" in argv and guard.VULTURE_IGNORE_NAMES in argv
    assert "--min-confidence" in argv and "60" in argv


# --- R2: path-anchored allowlist keys (no cross-site collision masking) ---


def test_vulture_finding_key_is_path_anchored() -> None:
    a = guard.VultureFinding("a.py", 1, "unused variable 'x'", 60)
    b = guard.VultureFinding("b.py", 2, "unused variable 'x'", 60)
    assert guard.vulture_finding_key(a) == "a.py:x"
    assert guard.vulture_finding_key(a) != guard.vulture_finding_key(b)


def test_filter_allowlisted_never_masks_same_name_in_another_module() -> None:
    a = guard.VultureFinding("a.py", 1, "unused variable 'your_label'", 60)
    b = guard.VultureFinding("b.py", 2, "unused variable 'your_label'", 60)
    # Grandfathering 'your_label' in a.py must leave b.py's still flagged.
    assert guard.filter_allowlisted([a, b], {"a.py:your_label"}) == [b]


def test_vulture_finding_key_unnamed_finding_is_not_a_grabbable_name() -> None:
    # "unreachable code after 'return'" has a quoted token that is NOT a symbol;
    # it must not become an allowlistable name (line-keyed, deliberately awkward).
    f = guard.VultureFinding("a.py", 7, "unreachable code after 'return'", 100)
    assert guard.vulture_finding_key(f) == "a.py:@7"


# --- R4: structural model-field guard (advisory-only) ---


def _classdef(src: str) -> ast.ClassDef:
    node = ast.parse(src).body[0]
    assert isinstance(node, ast.ClassDef)
    return node


def test_is_model_class_recognizes_basemodel_and_dataclass() -> None:
    assert guard._is_model_class(_classdef("class R(BaseModel):\n    x: int\n"))
    assert guard._is_model_class(_classdef("class R(pydantic.BaseModel):\n    x: int\n"))
    assert guard._is_model_class(_classdef("@dataclass\nclass R:\n    x: int\n"))
    assert guard._is_model_class(_classdef("@dataclass(frozen=True)\nclass R:\n    x: int\n"))
    assert not guard._is_model_class(_classdef("class R:\n    x: int\n"))
    assert not guard._is_model_class(_classdef("class R(SomethingElse):\n    x: int\n"))


def test_suppress_model_fields_drops_only_matching_advisory() -> None:
    field = guard.VultureFinding("m.py", 5, "unused variable 'field_a'", 60)
    method = guard.VultureFinding("m.py", 9, "unused method 'helper'", 60)
    assert guard.suppress_model_fields([field, method], {"m.py:field_a"}) == [method]


def test_model_field_keys_finds_a_real_response_field() -> None:
    # Integration over the real tree: a known dict-populated response field is
    # recognised by SHAPE, so it needs no per-symbol allowlist entry.
    keys = guard.model_field_keys(["zotero_summarizer/models/api.py"])
    assert "zotero_summarizer/models/api.py:config_loaded" in keys
