"""Shared synthetic Zotero schema for tests.

Mirrors the subset of Zotero's real schema that our feed-batch code touches.
Returns a path to a fresh sqlite DB file plus a Zotero-data-dir-shaped root.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def build_zotero_db(target_dir: Path) -> Path:
    """Create a Zotero-shaped sqlite DB under `target_dir/zotero.sqlite`.

    Returns the path. The dir also gets a `storage/` subdir like the real one.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "storage").mkdir(exist_ok=True)
    db_path = target_dir / "zotero.sqlite"
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE libraries (
            libraryID INTEGER PRIMARY KEY,
            type TEXT NOT NULL,
            editable INT NOT NULL DEFAULT 1,
            filesEditable INT NOT NULL DEFAULT 1,
            version INT NOT NULL DEFAULT 0,
            storageVersion INT NOT NULL DEFAULT 0,
            lastSync INT NOT NULL DEFAULT 0,
            archived INT NOT NULL DEFAULT 0,
            isAdmin INT NOT NULL DEFAULT 0
        );
        CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT NOT NULL UNIQUE);
        CREATE TABLE creatorTypes (creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT NOT NULL UNIQUE);
        CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT NOT NULL UNIQUE, fieldFormatID INT);
        CREATE TABLE items (
            itemID INTEGER PRIMARY KEY AUTOINCREMENT,
            itemTypeID INT NOT NULL,
            dateAdded TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            dateModified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            clientDateModified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            libraryID INT NOT NULL,
            key TEXT NOT NULL UNIQUE,
            version INT DEFAULT 0,
            synced INT DEFAULT 0
        );
        CREATE TABLE itemData (itemID INT, fieldID INT, valueID INT, PRIMARY KEY(itemID, fieldID));
        CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT);
        CREATE TABLE creators (
            creatorID INTEGER PRIMARY KEY AUTOINCREMENT,
            firstName TEXT,
            lastName TEXT,
            fieldMode INT DEFAULT 0
        );
        CREATE TABLE itemCreators (
            itemID INT, creatorID INT, creatorTypeID INT, orderIndex INT,
            PRIMARY KEY (itemID, creatorID, orderIndex)
        );
        CREATE TABLE collections (
            collectionID INTEGER PRIMARY KEY,
            collectionName TEXT NOT NULL,
            parentCollectionID INT DEFAULT NULL,
            clientDateModified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            libraryID INT NOT NULL,
            key TEXT NOT NULL,
            version INT DEFAULT 0,
            synced INT DEFAULT 0
        );
        CREATE TABLE collectionItems (itemID INT, collectionID INT, PRIMARY KEY(itemID, collectionID));
        CREATE TABLE tags (tagID INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, type INT DEFAULT 0);
        CREATE TABLE itemTags (itemID INT, tagID INT, type INT DEFAULT 0, PRIMARY KEY(itemID, tagID));
        CREATE TABLE itemNotes (itemID INT PRIMARY KEY, parentItemID INT, note TEXT, title TEXT);
        CREATE TABLE itemAttachments (
            itemID INTEGER PRIMARY KEY,
            parentItemID INT,
            linkMode INT,
            contentType TEXT,
            charsetID INT,
            path TEXT,
            syncState INT DEFAULT 0
        );
        CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY, dateDeleted TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE feeds (
            libraryID INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            lastUpdate TIMESTAMP,
            lastCheck TIMESTAMP,
            lastCheckError TEXT,
            cleanupReadAfter INT,
            cleanupUnreadAfter INT,
            refreshInterval INT DEFAULT 60
        );
        CREATE TABLE feedItems (
            itemID INTEGER PRIMARY KEY,
            guid TEXT NOT NULL,
            readTime TIMESTAMP,
            translatedTime TIMESTAMP
        );

        INSERT INTO libraries(libraryID, type) VALUES (1, 'user'), (2, 'feed'), (3, 'feed');
        INSERT INTO itemTypes(itemTypeID, typeName) VALUES
            (22, 'journalArticle'),
            (28, 'note'),
            (31, 'preprint');
        INSERT INTO creatorTypes(creatorTypeID, creatorType) VALUES (8, 'author');
        INSERT INTO fields(fieldID, fieldName) VALUES
            (1, 'title'),
            (2, 'abstractNote'),
            (3, 'url'),
            (4, 'DOI'),
            (5, 'date'),
            (6, 'publicationTitle'),
            (7, 'language');
        INSERT INTO feeds(libraryID, name, url, lastUpdate, lastCheck) VALUES
            (2, 'Test Feed A', 'http://example.com/feed-a.xml', '2026-05-12 10:00:00', '2026-05-12 12:00:00'),
            (3, 'Test Feed B', 'http://example.com/feed-b.xml', '2026-05-12 09:00:00', '2026-05-12 12:00:00');
        INSERT INTO collections(collectionID, collectionName, libraryID, key) VALUES
            (90, 'Inbox', 1, 'EQIM47Z6'),
            (91, 'Research', 1, 'RESEARCH1');
        """
    )
    conn.commit()
    conn.close()
    return db_path


def add_library_item(
    db_path: Path,
    *,
    item_key: str,
    title: str,
    doi: str | None = None,
    url: str | None = None,
    abstract: str | None = None,
    item_type: str = "journalArticle",
    library_id: int = 1,
) -> int:
    """Insert one regular library item with the given metadata. Returns itemID."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        type_row = conn.execute(
            "SELECT itemTypeID FROM itemTypes WHERE typeName=?", (item_type,)
        ).fetchone()
        item_type_id = int(type_row["itemTypeID"])

        cursor = conn.execute(
            "INSERT INTO items(itemTypeID, libraryID, key) VALUES (?,?,?)",
            (item_type_id, library_id, item_key),
        )
        item_id = int(cursor.lastrowid)

        for field_name, value in (
            ("title", title),
            ("abstractNote", abstract),
            ("url", url),
            ("DOI", doi),
        ):
            if value is None:
                continue
            field_row = conn.execute(
                "SELECT fieldID FROM fields WHERE fieldName=?", (field_name,)
            ).fetchone()
            value_row = conn.execute(
                "INSERT INTO itemDataValues(value) VALUES (?)", (value,)
            )
            conn.execute(
                "INSERT INTO itemData(itemID, fieldID, valueID) VALUES (?,?,?)",
                (item_id, int(field_row["fieldID"]), int(value_row.lastrowid)),
            )
        conn.commit()
        return item_id
    finally:
        conn.close()


def set_feed_item_read(db_path: Path, *, feed_item_id: int, read_time: str = "2026-05-13 10:00:00") -> None:
    """Mark a feed item as read by setting feedItems.readTime."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE feedItems SET readTime = ? WHERE itemID = ?", (read_time, feed_item_id))
        conn.commit()
    finally:
        conn.close()


def add_collection_link(db_path: Path, *, item_id: int, collection_id: int) -> None:
    """Link a library item to a collection."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO collectionItems(itemID, collectionID) VALUES (?, ?)",
            (item_id, collection_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_trashed(db_path: Path, *, item_id: int) -> None:
    """Move a library item to Zotero's trash."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("INSERT OR IGNORE INTO deletedItems(itemID) VALUES (?)", (item_id,))
        conn.commit()
    finally:
        conn.close()


def add_tag_to_item(db_path: Path, *, item_id: int, tag_name: str, tag_type: int = 0) -> None:
    """Tag a library item."""
    conn = sqlite3.connect(str(db_path))
    try:
        existing = conn.execute("SELECT tagID FROM tags WHERE name=?", (tag_name,)).fetchone()
        if existing:
            tag_id = int(existing[0])
        else:
            cur = conn.execute("INSERT INTO tags(name, type) VALUES (?, ?)", (tag_name, tag_type))
            tag_id = int(cur.lastrowid)
        conn.execute(
            "INSERT OR IGNORE INTO itemTags(itemID, tagID, type) VALUES (?, ?, ?)",
            (item_id, tag_id, tag_type),
        )
        conn.commit()
    finally:
        conn.close()


def add_feed(
    db_path: Path,
    *,
    library_id: int,
    name: str,
    url: str = "http://example.com/feed.xml",
) -> None:
    """Insert a feed row + matching library row (for tests that need custom feeds)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO libraries(libraryID, type) VALUES (?, 'feed')",
            (library_id,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO feeds(libraryID, name, url, lastUpdate, lastCheck)"
            " VALUES (?, ?, ?, '2026-05-01 00:00:00', '2026-05-01 00:00:00')",
            (library_id, name, url),
        )
        conn.commit()
    finally:
        conn.close()


def add_feed_item(
    db_path: Path,
    *,
    feed_library_id: int,
    item_id: int | None = None,
    guid: str | None = None,
    title: str = "Test Item",
    abstract: str = "",
    url: str = "",
    doi: str = "",
    publication_date: str = "",
    date_added: str = "2026-05-12 09:00:00",
) -> int:
    """Insert one feed item with metadata. Returns itemID.

    Either ``item_id`` or ``guid`` (or both) may be supplied.  When omitted,
    they default to a unique synthetic value so callers only have to specify
    what they care about.
    """
    if guid is None:
        guid = f"guid-{item_id or 0}-auto"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        type_row = conn.execute(
            "SELECT itemTypeID FROM itemTypes WHERE typeName='journalArticle'"
        ).fetchone()
        item_key = f"FK{abs(hash(guid)) % 10**6:06d}"
        if item_id is not None:
            conn.execute(
                "INSERT INTO items(itemID, itemTypeID, libraryID, key, dateAdded) VALUES (?,?,?,?,?)",
                (item_id, int(type_row["itemTypeID"]), feed_library_id, item_key, date_added),
            )
            inserted_id = item_id
        else:
            cursor = conn.execute(
                "INSERT INTO items(itemTypeID, libraryID, key, dateAdded) VALUES (?,?,?,?)",
                (int(type_row["itemTypeID"]), feed_library_id, item_key, date_added),
            )
            inserted_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO feedItems(itemID, guid) VALUES (?,?)",
            (inserted_id, guid),
        )
        for field_name, value in (
            ("title", title),
            ("abstractNote", abstract),
            ("url", url),
            ("DOI", doi),
            ("date", publication_date),
        ):
            if not value:
                continue
            field_row = conn.execute(
                "SELECT fieldID FROM fields WHERE fieldName=?", (field_name,)
            ).fetchone()
            value_row = conn.execute(
                "INSERT INTO itemDataValues(value) VALUES (?)", (value,)
            )
            conn.execute(
                "INSERT INTO itemData(itemID, fieldID, valueID) VALUES (?,?,?)",
                (inserted_id, int(field_row["fieldID"]), int(value_row.lastrowid)),
            )
        conn.commit()
        return inserted_id
    finally:
        conn.close()
