"""SQLite access with versioned, transactional migrations.

* One file (``weatherfit.db`` by default, override with ``WEATHERFIT_DB``) - SQLite ships with Python.
* ``Database.tx()`` opens an explicit ``BEGIN IMMEDIATE`` transaction (commit on success, rollback on error);
  ``Database.read()`` is for plain reads. Foreign keys are on, WAL mode and a busy timeout avoid "database is locked".
* ``migrate()`` applies numbered migrations once each, each inside its own transaction, records them in
  ``schema_migrations``, makes a ``.bak`` copy first when an existing database needs changes, and refuses to
  open a database created by a *newer* version of the app.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

APP_DIR = Path(__file__).resolve().parent
DEFAULT_DB = APP_DIR / "weatherfit.db"


def default_db_path() -> Path:
    """Resolved at call time so WEATHERFIT_DB can be set after import (tests, launchers)."""
    return Path(os.getenv("WEATHERFIT_DB") or DEFAULT_DB)


class DatabaseError(RuntimeError):
    pass


class DatabaseVersionError(DatabaseError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "clothing_items", (
        """CREATE TABLE clothing_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            image_path TEXT,
            image_sha256 TEXT,
            category TEXT NOT NULL,
            colour TEXT NOT NULL DEFAULT '',
            secondary_colour TEXT NOT NULL DEFAULT '',
            pattern TEXT NOT NULL DEFAULT 'solid',
            fabric TEXT NOT NULL DEFAULT 'other',
            season TEXT NOT NULL DEFAULT 'all',
            style TEXT NOT NULL DEFAULT 'Casual',
            occasion TEXT NOT NULL DEFAULT 'casual',
            gender_presentation TEXT NOT NULL DEFAULT 'unisex',
            comfort INTEGER NOT NULL DEFAULT 3 CHECK (comfort BETWEEN 1 AND 5),
            ownership TEXT NOT NULL DEFAULT 'wardrobe' CHECK (ownership IN ('wardrobe','inspiration')),
            source TEXT NOT NULL DEFAULT 'user',
            source_ref TEXT,
            license TEXT NOT NULL DEFAULT '',
            attribution TEXT NOT NULL DEFAULT '',
            label_source TEXT NOT NULL DEFAULT 'manual',
            uploaded_at TEXT NOT NULL,
            last_worn_date TEXT,
            usage_count INTEGER NOT NULL DEFAULT 0 CHECK (usage_count >= 0)
        )""",
        "CREATE INDEX idx_items_ownership_category ON clothing_items(ownership, category)",
        # the same photo cannot be added to the wardrobe twice; the same dataset record cannot be imported twice
        "CREATE UNIQUE INDEX uq_items_wardrobe_sha ON clothing_items(image_sha256) WHERE ownership='wardrobe' AND image_sha256 IS NOT NULL",
        "CREATE UNIQUE INDEX uq_items_source_ref ON clothing_items(source, source_ref) WHERE source_ref IS NOT NULL",
    )),
    Migration(2, "outfit_history", (
        """CREATE TABLE outfits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outfit_date TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('wardrobe','inspiration')),
            signature TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'recommended' CHECK (status IN ('recommended','worn','skipped')),
            favourite INTEGER NOT NULL DEFAULT 0 CHECK (favourite IN (0,1)),
            occasion TEXT NOT NULL DEFAULT '',
            weather_json TEXT NOT NULL DEFAULT '{}',
            colours TEXT NOT NULL DEFAULT '',
            categories TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            feedback TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (outfit_date, kind, signature)
        )""",
        """CREATE TABLE outfit_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outfit_id INTEGER NOT NULL REFERENCES outfits(id) ON DELETE CASCADE,
            item_id INTEGER REFERENCES clothing_items(id) ON DELETE SET NULL,
            role TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            colour TEXT NOT NULL DEFAULT '',
            owned INTEGER NOT NULL DEFAULT 1 CHECK (owned IN (0,1))
        )""",
        "CREATE INDEX idx_outfits_date ON outfits(outfit_date)",
        "CREATE INDEX idx_outfit_items_outfit ON outfit_items(outfit_id)",
        "CREATE INDEX idx_outfit_items_item ON outfit_items(item_id)",
    )),
    Migration(3, "embeddings_and_settings", (
        """CREATE TABLE item_embeddings (
            item_id INTEGER PRIMARY KEY REFERENCES clothing_items(id) ON DELETE CASCADE,
            model TEXT NOT NULL,
            dim INTEGER NOT NULL,
            vector BLOB NOT NULL
        )""",
        "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    )),
)
LATEST_VERSION = MIGRATIONS[-1].version


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    def __init__(self, path: str | Path | None = None, migrations: tuple[Migration, ...] = MIGRATIONS) -> None:
        self.path = Path(path) if path else default_db_path()
        self.migrations = migrations

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)  # explicit transaction control
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Write transaction: everything inside commits together or not at all."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ migrations
    def applied_versions(self) -> list[int]:
        with self.read() as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone():
                return []
            return [r["version"] for r in c.execute("SELECT version FROM schema_migrations ORDER BY version")]

    def migrate(self) -> list[int]:
        """Apply pending migrations. Returns the versions applied in this call."""
        known = {m.version for m in self.migrations}
        existed = self.path.exists() and self.path.stat().st_size > 0
        done = set(self.applied_versions())
        unknown = done - known
        if unknown:
            raise DatabaseVersionError(
                f"{self.path.name} was created by a newer WeatherFit AI (schema versions {sorted(unknown)}). "
                "Update the app or use a different database file; nothing was changed.")
        pending = [m for m in self.migrations if m.version not in done]
        if not pending:
            return []
        if existed and done:  # protect real data before altering it
            backup = self.path.with_suffix(self.path.suffix + f".bak-v{max(done)}")
            if not backup.exists():
                shutil.copy2(self.path, backup)
        applied: list[int] = []
        for m in sorted(pending, key=lambda m: m.version):
            with self.tx() as c:
                c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
                if c.execute("SELECT 1 FROM schema_migrations WHERE version=?", (m.version,)).fetchone():
                    continue  # another process applied it first
                try:
                    for stmt in m.statements:
                        c.execute(stmt)
                except sqlite3.DatabaseError as exc:
                    raise DatabaseError(f"Migration {m.version} ({m.name}) failed and was rolled back: {exc}") from exc
                c.execute("INSERT INTO schema_migrations(version, name, applied_at) VALUES (?,?,?)", (m.version, m.name, now_iso()))
            applied.append(m.version)
        return applied

    # ------------------------------------------------------------------ settings
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.read() as c:
            row = c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def init_db(path: str | Path | None = None) -> Database:
    db = Database(path)
    db.migrate()
    return db


if __name__ == "__main__":  # python db.py  -> create / upgrade the database
    d = init_db()
    print(f"Database ready at {d.path} (schema version {max(d.applied_versions())}).")
