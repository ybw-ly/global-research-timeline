"""SQLite storage for the research/report timeline (standard library only)."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("RESEARCH_DB", ROOT / "data" / "research.db"))


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = Path(path or DB_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(target), timeout=30, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS research_items (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            source_name TEXT,
            publisher TEXT,
            title TEXT NOT NULL,
            abstract TEXT DEFAULT '',
            authors TEXT,
            institutions TEXT,
            published_at TEXT NOT NULL,
            doi TEXT,
            report_no TEXT,
            url TEXT NOT NULL,
            pdf_url TEXT,
            doc_type TEXT NOT NULL DEFAULT 'paper',
            language TEXT,
            country TEXT,
            topics TEXT,
            fetched_at TEXT NOT NULL,
            fingerprint TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_research_doi
            ON research_items(lower(doi)) WHERE doi IS NOT NULL AND doi != '';
        CREATE INDEX IF NOT EXISTS ix_research_published
            ON research_items(published_at DESC);
        CREATE INDEX IF NOT EXISTS ix_research_source
            ON research_items(source_id);
        CREATE INDEX IF NOT EXISTS ix_research_doc_type
            ON research_items(doc_type);

        CREATE TABLE IF NOT EXISTS research_sources (
            id TEXT PRIMARY KEY,
            name TEXT,
            publisher TEXT,
            adapter TEXT,
            query TEXT,
            homepage TEXT,
            country TEXT,
            language TEXT,
            doc_type TEXT,
            refresh_minutes INTEGER DEFAULT 360,
            max_items INTEGER DEFAULT 20,
            enabled INTEGER DEFAULT 1,
            last_fetch_at TEXT,
            last_status TEXT,
            last_error TEXT,
            last_count INTEGER DEFAULT 0,
            total_fetched INTEGER DEFAULT 0
        );
        """
    )
    conn.commit()
