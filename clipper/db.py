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
    layout TEXT NOT NULL DEFAULT 'original',
    crop_pos REAL NOT NULL DEFAULT 0.5,
    campaign_id INTEGER,
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
    layout TEXT NOT NULL DEFAULT 'original',
    crop_pos REAL NOT NULL DEFAULT 0.5,
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
    status TEXT NOT NULL DEFAULT 'suggested',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_name TEXT,
    source_text TEXT NOT NULL,
    rules_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'extracting',
    message TEXT, error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS creator_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    audience TEXT NOT NULL DEFAULT 'general',
    target_seconds INTEGER NOT NULL DEFAULT 60,
    status TEXT NOT NULL DEFAULT 'draft',
    script_json TEXT,
    error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS creator_stages (
    project_id INTEGER NOT NULL REFERENCES creator_projects(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    artifact_json TEXT,
    error TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(project_id, stage)
);
CREATE TABLE IF NOT EXISTS clip_compliance (
    clip_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
    report_json TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

# Columns added in later stages: upgraded in place so existing data is kept.
UPGRADES = {
    "projects": {
        "layout": "TEXT NOT NULL DEFAULT 'original'",
        "crop_pos": "REAL NOT NULL DEFAULT 0.5",
        "campaign_id": "INTEGER",
    },
    "clips": {
        "burn_captions": "INTEGER NOT NULL DEFAULT 0",
        "note": "TEXT",
        "layout": "TEXT NOT NULL DEFAULT 'original'",
        "crop_pos": "REAL NOT NULL DEFAULT 0.5",
    },
    "highlights": {
        "status": "TEXT NOT NULL DEFAULT 'suggested'",
    },
}


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

        for table, columns in UPGRADES.items():
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

        # Jobs that were running when the app last stopped can never finish.
        for table in ("clips", "transcripts", "analyses"):
            conn.execute(
                f"UPDATE {table} SET status='failed', error='Interrupted by app restart' "
                "WHERE status IN ('queued', 'processing')"
            )
        conn.execute(
            "UPDATE campaigns SET status='failed', error='Interrupted by app restart. "
            "You can read the document again or fill in the rules by hand.' "
            "WHERE status = 'extracting'"
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