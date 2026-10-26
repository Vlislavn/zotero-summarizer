"""Keep the main documentation small and its local links navigable."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]


def maintained_documents():
    documents = [ROOT / name for name in ("README.md", "CONTRIBUTING.md", "tests/README.md",
                                         "frontend/README.md", "frontend/DESIGN.md")]
    documents.extend((ROOT / "docs").rglob("*.md"))
    for directory in ("zotero_summarizer", "tools"):
        documents.extend((ROOT / directory).rglob("README.md"))
        documents.extend((ROOT / directory).rglob("*INTERFACES.md"))
    return sorted(set(documents))


def heading_anchors(text):
    anchors, counts = set(), {}
    for heading in re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE):
        slug = re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        anchors.add(f"{slug}-{count}" if count else slug)
        counts[slug] = count + 1
    anchors.update(re.findall(r'<a\s+(?:id|name)=["\x27]([^"\x27]+)', text))
    return anchors


def test_docs_directory_contains_three_main_documents_and_declared_validation_artifacts():
    files = {path.relative_to(ROOT / "docs").as_posix()
             for path in (ROOT / "docs").rglob("*") if path.is_file() and path.name != ".DS_Store"}
    validation = {
        "ui-fleet50": {"README.md", "results.json", "findings.json", "coverage.json", "source-manifest.json"},
        "pr208": {"README.md", "manual-checklist.md", "issue-scope.md", "results.json", "swarm.json", "source-manifest.json"},
    }
    assert files == {"architecture.md", "features.md", "overrides.md"} | {
        f"validation/{directory}/{name}" for directory, names in validation.items() for name in names
    }


def test_features_group_single_paragraph_entries_under_task_headings():
    text = (ROOT / "docs/features.md").read_text(encoding="utf-8")
    groups = re.split(r"^## .+\n", text, flags=re.MULTILINE)[1:]
    assert groups
    for group in groups:
        introduction, *features = re.split(r"^### .+\n", group, flags=re.MULTILINE)
        assert not introduction.strip(), group
        assert features, group
        for feature in features:
            paragraphs = [block for block in re.split(r"\n\s*\n", feature.strip()) if block.strip()]
            assert len(paragraphs) == 1, feature


def test_maintained_documentation_links_resolve():
    failures = []
    for document in maintained_documents():
        text = re.sub(r"```.*?```", "", document.read_text(encoding="utf-8"), flags=re.DOTALL)
        for destination in re.findall(r"\[[^\]\n]*\]\(([^\s)]+)\)", text):
            link = urlsplit(destination)
            if link.scheme or link.netloc:
                continue
            target = document.parent / unquote(link.path) if link.path else document
            if not target.exists():
                failures.append(f"{document.relative_to(ROOT)}: missing {destination}")
            elif link.fragment and target.suffix == ".md":
                if unquote(link.fragment) not in heading_anchors(target.read_text(encoding="utf-8")):
                    failures.append(f"{document.relative_to(ROOT)}: missing anchor {destination}")
    assert not failures, "\n".join(failures)
