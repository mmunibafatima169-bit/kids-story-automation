"""SQLite schema and connection management."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1


class ClosingConnection(sqlite3.Connection):
    """SQLite transaction context that also releases the file handle on exit."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self.path, timeout=15, isolation_level=None, factory=ClosingConnection
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema {version} is newer than supported {SCHEMA_VERSION}."
                )
            if version == 0:
                try:
                    conn.executescript(
                        """
                        BEGIN IMMEDIATE;
                        CREATE TABLE IF NOT EXISTS jobs (
                            id TEXT PRIMARY KEY,
                            topic TEXT NOT NULL,
                            question TEXT NOT NULL DEFAULT '',
                            status TEXT NOT NULL,
                            idempotency_key TEXT NOT NULL UNIQUE,
                            attempts INTEGER NOT NULL DEFAULT 0,
                            output_dir TEXT NOT NULL,
                            metadata_json TEXT NOT NULL DEFAULT '{}',
                            error TEXT NOT NULL DEFAULT '',
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS jobs_status_updated
                            ON jobs(status, updated_at);
                        CREATE TABLE IF NOT EXISTS topic_proposals (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            topic TEXT NOT NULL,
                            question TEXT NOT NULL,
                            score REAL NOT NULL,
                            proposal_json TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            UNIQUE(topic, question)
                        );
                        CREATE TABLE IF NOT EXISTS job_events (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                            from_status TEXT NOT NULL,
                            to_status TEXT NOT NULL,
                            detail TEXT NOT NULL DEFAULT '',
                            created_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS sources (
                            id TEXT PRIMARY KEY,
                            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                            title TEXT NOT NULL,
                            url TEXT NOT NULL,
                            publisher TEXT NOT NULL,
                            accessed_at TEXT NOT NULL,
                            published_at TEXT NOT NULL DEFAULT '',
                            evidence TEXT NOT NULL,
                            notes TEXT NOT NULL DEFAULT ''
                        );
                        CREATE TABLE IF NOT EXISTS claims (
                            id TEXT PRIMARY KEY,
                            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                            text TEXT NOT NULL,
                            kind TEXT NOT NULL,
                            source_ids_json TEXT NOT NULL DEFAULT '[]',
                            uncertainty TEXT NOT NULL DEFAULT ''
                        );
                        CREATE TABLE IF NOT EXISTS assets (
                            id TEXT PRIMARY KEY,
                            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                            path TEXT NOT NULL,
                            origin_url TEXT NOT NULL,
                            creator TEXT NOT NULL,
                            licence TEXT NOT NULL,
                            attribution TEXT NOT NULL,
                            commercial_use INTEGER NOT NULL,
                            file_sha256 TEXT NOT NULL,
                            retrieved_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS approvals (
                            id TEXT PRIMARY KEY,
                            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                            package_sha256 TEXT NOT NULL,
                            decision TEXT NOT NULL,
                            reviewer TEXT NOT NULL,
                            note TEXT NOT NULL DEFAULT '',
                            created_at TEXT NOT NULL,
                            invalidated_at TEXT NOT NULL DEFAULT ''
                        );
                        CREATE TABLE IF NOT EXISTS uploads (
                            id TEXT PRIMARY KEY,
                            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                            provider TEXT NOT NULL,
                            remote_id TEXT NOT NULL DEFAULT '',
                            status TEXT NOT NULL,
                            detail TEXT NOT NULL DEFAULT '',
                            created_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS analytics (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
                            imported_at TEXT NOT NULL,
                            views INTEGER,
                            click_through_rate REAL,
                            watch_time_minutes REAL,
                            subscribers_gained INTEGER,
                            notes TEXT NOT NULL DEFAULT ''
                        );
                        PRAGMA user_version = 1;
                        COMMIT;
                        """
                    )
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
