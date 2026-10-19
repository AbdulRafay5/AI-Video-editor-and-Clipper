import sqlite3
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    source_path TEXT NOT NULL,
    duration REAL, width INTEGER, height INTEGER, size_bytes INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0,
    output_path TEXT,
    error TEXT,
    encoder TEXT,
    burn_captions INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS transcripts (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0,
    model TEXT, device TEXT, language TEXT,
    result_json TEXT,
    error TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS captions (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    captions_json TEXT NOT NULL,
    style_json TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS analyses (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0,
    message TEXT, error TEXT, model TEXT, params_json TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS highlights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    duration REAL NOT NULL,
    category TEXT, score INTEGER, llm_score INTEGER,
    reason TEXT, note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)

        # Upgrade databases created by earlier stages.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(clips)")}
        if "burn_captions" not in cols:
            conn.execute("ALTER TABLE clips ADD COLUMN burn_captions INTEGER NOT NULL DEFAULT 0")
        if "note" not in cols:
            conn.execute("ALTER TABLE clips ADD COLUMN note TEXT")

        # Jobs that were running when the app last stopped can never finish.
        conn.execute(
            "UPDATE clips SET status='failed', error='Interrupted by app restart' "
            "WHERE status IN ('queued', 'processing')"
        )
        conn.execute(
            "UPDATE transcripts SET status='failed', error='Interrupted by app restart' "
            "WHERE status IN ('queued', 'processing')"
        )
        conn.execute(
            "UPDATE analyses SET status='failed', error='Interrupted by app restart' "
            "WHERE status IN ('queued', 'processing')"
        )


def query(sql, params=()):
    with get_db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    with get_db() as conn:
        return conn.execute(sql, params).lastrowid