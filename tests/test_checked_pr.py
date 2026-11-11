"""Exercise the real Makefile/publication entrypoint against executable check doubles."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKS = [
    ("pre-commit", ["run", "--all-files"]),
    ("pytest", ["-q", "--forked"]),
    ("npm", ["run", "lint"]),
    ("npm", ["test"]),
    ("npm", ["run", "build"]),
    ("zotero-summarizer", ["smoke-test", "--project-root", "."]),
]


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    repo, binaries = tmp_path / "checkout", tmp_path / "bin"
    (repo / "tools").mkdir(parents=True)
    (repo / "frontend").mkdir()
    binaries.mkdir()
    shutil.copy(ROOT / "Makefile", repo / "Makefile")
    shutil.copy(ROOT / "tools/create-pr.sh", repo / "tools/create-pr.sh")
    (repo / "input.txt").write_text("original\n")
    (repo / ".gitignore").write_text("frontend/dist/\n")
    git(repo, "init", "-b", "topic")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "core.hooksPath", "/dev/null")
    git(repo, "add", ".")
    git(repo, "commit", "--no-verify", "-m", "fixture")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(origin))
    body = tmp_path / "body with spaces.md"
    body.write_text("Why: test.\n")
    fake = binaries / "fake"
    fake.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, subprocess, sys
name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
entry = {"name": name, "args": args, "cwd": os.getcwd(),
         "kmp": os.environ.get("KMP_DUPLICATE_LIB_OK"), "pythonpath": os.environ.get("PYTHONPATH")}
with open(os.environ["EVENTS"], "a") as stream:
    stream.write(json.dumps(entry) + "\\n")
if name == "pre-commit":
    mutation = os.environ.get("MUTATION")
    if mutation == "tracked": pathlib.Path("input.txt").write_text("checker edit\\n")
    if mutation == "untracked": pathlib.Path("new.py").write_text("checker edit\\n")
    if mutation == "commit": subprocess.run(["git", "commit", "--allow-empty", "--no-verify", "-m", "other"], check=True)
    if mutation == "branch": subprocess.run(["git", "switch", "-c", "other"], check=True)
if name == "npm" and args[:2] == ["run", "build"]:
    output = pathlib.Path(args[args.index("--outDir") + 1])
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text("build output")
key = name + " " + " ".join(args[:2])
sys.exit(int(os.environ.get("FAIL_STATUS", "37")) if key == os.environ.get("FAIL_CHECK") or name == os.environ.get("FAIL_CHECK") else 0)
''')
    fake.chmod(0o755)
    for name in ["pre-commit", "pytest", "npm", "gh", "zotero-summarizer"]:
        (binaries / name).symlink_to(fake)
    events = tmp_path / "events.jsonl"
    environment = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                   "EVENTS": str(events), "TMPDIR": str(tmp_path), "MAKEFLAGS": "-j8"}
    for variable in ["FAIL_CHECK", "FAIL_STATUS", "MUTATION"]:
        environment.pop(variable, None)
    return repo, body, events, environment


def publish(checkout, **environment):
    repo, body, events, env = checkout
    result = subprocess.run([str(repo / "tools/create-pr.sh"), "Title ; $(not a shell) ✨", str(body)],
                            cwd=repo / "frontend", env={**env, **environment}, capture_output=True, text=True)
    recorded = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
    return result, recorded


def test_success_runs_all_checks_serially_then_publishes(checkout):
    repo, body, _, _ = checkout
    original = git(repo, "rev-parse", "HEAD")
    dist = repo / "frontend/dist/index.html"
    dist.parent.mkdir()
    dist.write_text("existing user build")
    result, recorded = publish(checkout)
    assert result.returncode == 0, result.stderr
    assert [r["name"] for r in recorded] == [c[0] for c in CHECKS] + ["gh"]
    for entry, (name, args) in zip(recorded, CHECKS):
        assert entry["args"][:len(args)] == args
        assert entry["cwd"] == str(repo / "frontend" if name == "npm" else repo)
    assert recorded[1]["kmp"] == "TRUE"
    assert recorded[-2]["pythonpath"].split(os.pathsep)[0] == str(repo)
    build_args = recorded[4]["args"]
    assert build_args[3:4] == ["--outDir"]
    assert Path(build_args[4]).parent == repo.parent
    assert not Path(build_args[4]).exists()
    assert dist.read_text() == "existing user build"
    assert recorded[-1]["args"] == ["pr", "create", "--repo", str(repo.parent / "origin.git"),
                                      "--base", "main", "--head", "topic", "--title",
                                      "Title ; $(not a shell) ✨", "--body-file", str(body)]
    assert git(repo, "ls-remote", "origin", "refs/heads/topic").split()[0] == original
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "stash", "list") == ""


@pytest.mark.parametrize("failed", ["pre-commit", "pytest", "npm run lint", "npm test", "npm run build", "zotero-summarizer"])
def test_each_actual_check_failure_stops_publication(checkout, failed):
    repo, _, _, _ = checkout
    result, recorded = publish(checkout, FAIL_CHECK=failed)
    assert result.returncode != 0
    expected = [c[0] + " " + " ".join(c[1][:2]) for c in CHECKS]
    index = next(i for i, key in enumerate(expected) if key == failed or key.startswith(failed + " "))
    assert len(recorded) == index + 1
    assert recorded[-1]["name"] != "gh"
    assert git(repo, "ls-remote", "origin", "refs/heads/topic") == ""
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "stash", "list") == ""
    if failed == "npm run build":
        assert not Path(recorded[-1]["args"][4]).exists()


@pytest.mark.parametrize("dirt", ["staged", "unstaged", "untracked"])
def test_dirty_input_is_refused_unchanged(checkout, dirt):
    repo, _, _, _ = checkout
    target = repo / ("new.py" if dirt == "untracked" else "input.txt")
    target.write_text("user edit\n")
    if dirt == "staged":
        git(repo, "add", "input.txt")
    before = git(repo, "status", "--porcelain"), git(repo, "write-tree")
    result, recorded = publish(checkout)
    assert result.returncode != 0
    assert recorded == []
    assert (git(repo, "status", "--porcelain"), git(repo, "write-tree")) == before
    assert target.read_text() == "user edit\n"
    assert git(repo, "stash", "list") == ""


@pytest.mark.parametrize("mutation", ["tracked", "untracked", "commit", "branch"])
def test_changed_source_identity_blocks_publication_without_reverting(checkout, mutation):
    repo, _, _, _ = checkout
    before = git(repo, "rev-parse", "HEAD")
    result, recorded = publish(checkout, MUTATION=mutation)
    assert result.returncode != 0
    assert "gh" not in [r["name"] for r in recorded]
    assert git(repo, "ls-remote", "origin", "refs/heads/topic") == ""
    if mutation == "tracked":
        assert (repo / "input.txt").read_text() == "checker edit\n"
    if mutation == "untracked":
        assert (repo / "new.py").read_text() == "checker edit\n"
    if mutation == "commit":
        assert git(repo, "rev-parse", "HEAD") != before
    if mutation == "branch":
        assert git(repo, "branch", "--show-current") == "other"
    assert git(repo, "stash", "list") == ""


def test_parallel_make_check_keeps_the_actual_order(checkout):
    repo, _, events, env = checkout
    result = subprocess.run(["make", "-j8", "check"], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    recorded = [json.loads(line) for line in events.read_text().splitlines()]
    assert [r["name"] for r in recorded] == [c[0] for c in CHECKS]


@pytest.mark.parametrize("flags", ["-n", "-i"])
def test_inherited_make_flags_cannot_bypass_failed_checks(checkout, flags):
    result, recorded = publish(checkout, MAKEFLAGS=flags, FAIL_CHECK="pre-commit")
    assert result.returncode != 0
    assert [r["name"] for r in recorded] == ["pre-commit"]


def test_interrupted_check_stops_publication(checkout):
    result, recorded = publish(checkout, FAIL_CHECK="pytest", FAIL_STATUS="130")
    assert result.returncode != 0
    assert [r["name"] for r in recorded] == ["pre-commit", "pytest"]


def test_missing_check_tool_is_not_skipped(checkout):
    repo, _, _, _ = checkout
    binaries = repo.parent / "bin"
    (binaries / "pre-commit").unlink()
    result, recorded = publish(checkout, PATH=str(binaries) + ":/usr/bin:/bin")
    assert result.returncode != 0
    assert recorded == []


def test_publisher_exit_status_propagates(checkout):
    result, recorded = publish(checkout, FAIL_CHECK="gh", FAIL_STATUS="37")
    assert result.returncode == 37
    assert recorded[-1]["name"] == "gh"


def test_optional_ci_uses_the_same_check_owner():
    import yaml

    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    steps = workflow["jobs"]["checks"]["steps"]
    assert steps[-1]["run"] == "uv run make check"
    package = json.loads((ROOT / "frontend/package.json").read_text())
    assert set(["lint", "test", "build"]) <= package["scripts"].keys()


def test_push_failure_stops_publisher(checkout):
    repo, _, _, _ = checkout
    git(repo, "remote", "set-url", "origin", str(repo.parent / "missing.git"))
    result, recorded = publish(checkout)
    assert result.returncode != 0
    assert [r["name"] for r in recorded] == [c[0] for c in CHECKS]


@pytest.mark.parametrize("branch", ["main", "detached"])
def test_refuses_base_or_detached_head(checkout, branch):
    repo, _, _, _ = checkout
    if branch == "main":
        git(repo, "branch", "-m", "main")
    else:
        git(repo, "checkout", "--detach")
    result, recorded = publish(checkout)
    assert result.returncode != 0
    assert recorded == []
