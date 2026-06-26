"""Pydantic / dataclass model-field detection for the Vulture dead-code gate.

Extracted from check_dead_code.py to keep that file under the 500-LOC limit.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = "zotero_summarizer"


def _is_model_class(node: ast.ClassDef) -> bool:
    """True when ``node`` is a direct pydantic ``BaseModel`` subclass or ``@dataclass``."""
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name) and target.id == "dataclass":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "dataclass":
            return True
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "BaseModel":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "BaseModel":
            return True
    return False


def _all_runtime_python_files() -> list[str]:
    """All .py files under RUNTIME_ROOT, tracked or untracked (mirrors Vulture's scope)."""
    return [
        str(p.relative_to(REPO_ROOT).as_posix())
        for p in (REPO_ROOT / RUNTIME_ROOT).rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def model_field_keys(paths: list[str]) -> set[str]:
    """Return ``<path>:<name>`` for every class-level annotated field of a
    pydantic ``BaseModel`` / ``@dataclass`` across ``paths``.

    Vulture flags these (the framework populates + serialises them; they are
    never read by name). Recognising the *shape* — an ``AnnAssign`` field of a
    model class — is a structural guard, not a symbol list, so new response
    fields are covered with no allowlist churn.

    Handles transitive inheritance within each file (e.g. ``class B(A)`` where
    ``A`` itself is a ``BaseModel`` subclass — Vulture flags fields of ``B``
    even though they are framework-managed).
    """
    keys: set[str] = set()
    for path in paths:
        tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
        posix = Path(path).as_posix()
        classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        # Expand model set until stable: handles A(BaseModel) → B(A) → C(B).
        model_names: set[str] = set()
        changed = True
        while changed:
            changed = False
            for name, node in classes.items():
                if name in model_names:
                    continue
                if _is_model_class(node):
                    model_names.add(name)
                    changed = True
                    continue
                for base in node.bases:
                    base_name = (
                        base.id if isinstance(base, ast.Name)
                        else base.attr if isinstance(base, ast.Attribute)
                        else None
                    )
                    if base_name and base_name in model_names:
                        model_names.add(name)
                        changed = True
                        break
        for name in model_names:
            for stmt in classes[name].body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    keys.add(f"{posix}:{stmt.target.id}")
    return keys
