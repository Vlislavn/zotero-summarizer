"""Owner-supplied CSV/XLSX import: merge by number (else title), idempotent, survives an Update,
and the imported text stays under ``data/conferences/`` (REQ-S36)."""
from __future__ import annotations

import io
import zipfile

import httpx

from zotero_summarizer import runtime
from zotero_summarizer.services.conferences import service
from zotero_summarizer.settings import Settings

MARKER = "ZS-IMPORT-MARKER-51c2"
CSV = (f"Poster ID,Poster Title,Authors,Category,Abstract\n"
       f"5P,Poster A,X Y; Z W,Poster,Text A {MARKER}\n"
       f"LBA1,,,Late,Text B\n"
       f",poster  a!,,,Text A again {MARKER}\n"
       f"CN7,Case study,Q R,Poster,Text C\n"
       f",,,,orphan text\n").encode()


def _setup(tmp_path, block: str) -> Settings:
    settings = Settings.load(project_root=tmp_path)
    runtime.set_context(runtime.AppContext(settings=settings))
    settings.conferences_dir.mkdir(parents=True)
    settings.conferences_config_path.write_text("conferences:\n" + block)
    return settings


def _crossref() -> httpx.Client:
    items = [{"DOI": "10.1016/j.annonc.2025.08.442", "title": ["5P Poster A"], "page": "S209", "author": []},
             {"DOI": "10.1016/j.annonc.2025.08.1590", "title": ["LBA1 Trial B"], "page": "S1", "author": []}]
    return httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"message": {"items": items, "next-cursor": "", "total-results": 2}})))


def _records(view: dict) -> list[dict]:
    return sorted(view["items"], key=lambda r: r["number"])


def test_csv_import_merges_by_number_then_title_and_is_idempotent(tmp_path):
    settings = _setup(tmp_path, "  - {id: esmo, recipe: esmo, editions: {2025: {from: 2025-09-01, until: 2025-09-30}}}\n")
    service.refresh("esmo", 2025, http_client=_crossref())
    assert service.overview()["conferences"][0]["editions"][0]["abstract_available"] is False

    first = service.import_file("esmo", 2025, name="abstracts.csv", data=CSV)
    assert (first["rows"], first["matched"], first["added"], first["skipped"]) == (5, 3, 1, 1)
    view = service.edition_view("esmo", 2025, page=1, page_size=25, query="")
    rows = _records(view)
    assert [r["number"] for r in rows] == ["5P", "CN7", "LBA1"] and view["summary"]["abstract_available"] is True
    by_number = {r["number"]: r for r in rows}
    assert by_number["5P"]["session"] == "Poster" and by_number["5P"]["authors"] == ["X Y", "Z W"]
    assert by_number["CN7"]["id"].startswith("file:") and by_number["CN7"]["url"] == ""
    assert MARKER in service.abstract_of("esmo", 2025, by_number["5P"]["id"])  # the file's text wins

    second = service.import_file("esmo", 2025, name="abstracts.csv", data=CSV)
    assert (second["matched"], second["added"], second["skipped"]) == (4, 0, 1)
    assert _records(service.edition_view("esmo", 2025, page=1, page_size=25, query="")) == rows

    service.refresh("esmo", 2025, http_client=_crossref())  # an Update keeps what was imported
    after = service.edition_view("esmo", 2025, page=1, page_size=25, query="")
    assert _records(after) == rows

    holders = {p for p in tmp_path.rglob("*") if p.is_file() and MARKER.encode() in p.read_bytes()}
    assert holders and all(settings.conferences_dir in p.parents for p in holders)
    assert not settings.corpus_db_path.exists() and not settings.triage_db_path.exists()


def _xlsx(rows: list[list]) -> bytes:
    strings: list[str] = []
    cells = []
    for r, row in enumerate(rows, 1):
        cs = []
        for c, value in enumerate(row):
            ref = f"{'ABCDEF'[c]}{r}"
            if isinstance(value, str):
                strings.append(value)
                cs.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            else:
                cs.append(f'<c r="{ref}"><v>{value}</v></c>')
        cells.append(f'<row r="{r}">{"".join(cs)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as book:
        book.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>{"".join(cells)}</sheetData></worksheet>')
        book.writestr("xl/sharedStrings.xml", f'<sst {ns}>{"".join(f"<si><t>{s}</t></si>" for s in strings)}</sst>')
    return out.getvalue()


def test_xlsx_import_into_an_edition_without_a_download(tmp_path):
    _setup(tmp_path, "  - {id: asco, recipe: asco, editions: {2026: {from: 2026-05-20, until: 2026-06-30}}}\n")
    data = _xlsx([["Abstract #", "Title", "Abstract text", "DOI"],
                  [8114, "A numbered trial", "Body", "https://doi.org/10.1200/jco.2026.44.17_suppl.8114"],
                  ["LBA2", "A late trial", "Body 2", ""]])
    result = service.import_file("asco", 2026, name="book.xlsx", data=data)
    assert (result["ok"], result["rows"], result["added"]) == (True, 2, 2)
    view = service.edition_view("asco", 2026, page=1, page_size=25, query="")
    assert view["counts"]["late_breaking"] == 1 and {r["number"] for r in view["items"]} == {"8114", "LBA2"}
    assert service.abstract_of("asco", 2026, "10.1200/jco.2026.44.17_suppl.8114") == "Body"
