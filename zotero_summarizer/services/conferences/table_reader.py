"""Read an owner-supplied CSV or XLSX (standard library only) into rows of known fields.

Columns are found by header name, tolerant of case, spaces and punctuation (``Poster ID`` and
``abstract_number`` are both the number). XLSX: the first worksheet, shared or inline strings.
Raises ``ValueError`` with a message for the owner when the file cannot be used.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Iterator
from xml.etree import ElementTree

FIELDS = ("number", "title", "authors", "abstract", "doi", "session")
_ALIASES = {  # header with only letters and digits, lower case
    "number": {"number", "no", "num", "nr", "id", "abstractid", "abstractnumber", "abstractno", "posterid",
               "posternumber", "presentationnumber", "code"},
    "title": {"title", "postertitle", "abstracttitle", "papertitle", "presentationtitle"},
    "authors": {"authors", "author", "authorlist", "authorblock"},
    "abstract": {"abstract", "abstracttext", "text"},
    "doi": {"doi"},
    "session": {"session", "sessiontitle", "sessiontype", "category", "track"},
}
_MAX_UNPACKED = 300 * 1024 * 1024  # an xlsx that unpacks to more than this is refused


def _header_key(cell: str) -> str:
    return re.sub(r"[^a-z0-9]", "", cell.replace("#", "number").casefold())


def _columns(header: list[str]) -> dict[str, int]:
    keys = [_header_key(cell) for cell in header]
    found = {field: next((i for i, k in enumerate(keys) if k in names), -1) for field, names in _ALIASES.items()}
    if found["number"] < 0 and found["title"] < 0:
        raise ValueError("No 'number' or 'title' column found. Columns read: " + (", ".join(c for c in header if c) or "none"))
    return {field: i for field, i in found.items() if i >= 0}


def _csv_lines(data: bytes) -> list[list[str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    first = text.split("\n", 1)[0]
    delimiter = max(",;\t", key=first.count)
    return list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _string_item(item: ElementTree.Element) -> str:
    texts = []
    for child in item:  # plain <t> or rich-text <r><t>; phonetic runs (<rPh>) are skipped
        if _local(child.tag) == "t":
            texts.append(child)
        elif _local(child.tag) == "r":
            texts += [t for t in child if _local(t.tag) == "t"]
    return "".join(t.text or "" for t in texts)


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in book.namelist():
        return []
    strings = []
    for _, item in ElementTree.iterparse(book.open("xl/sharedStrings.xml"), events=("end",)):
        if _local(item.tag) == "si":
            strings.append(_string_item(item))
            item.clear()
    return strings


def _column_index(ref: str) -> int:
    index = 0
    for letter in re.match(r"[A-Za-z]+", ref).group().upper():
        index = index * 26 + ord(letter) - 64
    return index - 1


def _cell_text(cell: ElementTree.Element, shared: list[str]) -> str:
    value = next((c for c in cell if _local(c.tag) == "v"), None)
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter() if _local(t.tag) == "t")
    if value is None or value.text is None:
        return ""
    return shared[int(value.text)] if kind == "s" else value.text


def _xlsx_lines(data: bytes) -> Iterator[list[str]]:
    try:
        book = zipfile.ZipFile(io.BytesIO(data))
        if sum(i.file_size for i in book.infolist()) > _MAX_UNPACKED:
            raise ValueError("The xlsx file is too large when unpacked")
        sheets = sorted((n for n in book.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                        key=lambda n: int(re.findall(r"\d+", n)[0]))
        if not sheets:
            raise ValueError("The xlsx file has no worksheet")
        shared = _shared_strings(book)
        for _, row in ElementTree.iterparse(book.open(sheets[0]), events=("end",)):
            if _local(row.tag) == "row":
                line: list[str] = []
                for cell in row:
                    at = _column_index(cell.get("r") or "A") if cell.get("r") else len(line)
                    line += [""] * (at - len(line))
                    line.append(_cell_text(cell, shared))
                yield line
                row.clear()
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, IndexError, AttributeError) as exc:
        raise ValueError("The file is not a readable .xlsx workbook") from exc


def read_rows(name: str, data: bytes) -> list[dict[str, str]]:
    """Non-empty data rows as ``{field: text}`` (only fields whose column exists), in file order."""
    lower = name.lower()
    if lower.endswith(".xlsx"):
        lines = list(_xlsx_lines(data))
    elif lower.endswith(".csv"):
        lines = _csv_lines(data)
    else:
        raise ValueError("Choose a .csv or .xlsx file")
    header = next((line for line in lines if any(c.strip() for c in line)), None)
    if header is None:
        raise ValueError("The file is empty")
    columns = _columns(header)
    rows = []
    for line in lines[lines.index(header) + 1:]:
        row = {field: (line[i].strip() if i < len(line) else "") for field, i in columns.items()}
        if any(row.values()):
            rows.append(row)
    return rows
