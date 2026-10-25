import sqlite3

import pytest

import wardrobe as W
from db import Database, DatabaseError, DatabaseVersionError, LATEST_VERSION, Migration, MIGRATIONS


def test_fresh_database_gets_all_migrations(tmp_path):
    d = Database(tmp_path / "a.db")
    assert d.migrate() == [m.version for m in MIGRATIONS]
    assert d.applied_versions() == list(range(1, LATEST_VERSION + 1))


def test_migrate_is_idempotent(db):
    assert db.migrate() == []
    assert db.applied_versions() == list(range(1, LATEST_VERSION + 1))


def test_upgrade_keeps_data_and_makes_backup(tmp_path):
    old = Database(tmp_path / "a.db", migrations=MIGRATIONS[:1])
    old.migrate()
    with old.tx() as c:
        c.execute("INSERT INTO clothing_items(category, uploaded_at) VALUES ('shirt','2026-01-01T00:00:00Z')")
    new = Database(tmp_path / "a.db")
    assert new.migrate() == [2, 3]
    assert len(W.list_items(new, None)) == 1
    assert (tmp_path / "a.db.bak-v1").exists()


def test_failed_migration_rolls_back_and_is_not_recorded(tmp_path):
    bad = Migration(2, "bad", ("CREATE TABLE ok_table (id INTEGER)", "CREATE TABLE ok_table (id INTEGER)"))
    d = Database(tmp_path / "a.db", migrations=(MIGRATIONS[0], bad))
    with pytest.raises(DatabaseError):
        d.migrate()
    assert d.applied_versions() == [1]
    with d.read() as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='ok_table'").fetchone()


def test_database_from_newer_version_is_refused(tmp_path):
    newer = Database(tmp_path / "a.db", migrations=MIGRATIONS + (Migration(99, "future", ("CREATE TABLE future_t (x INTEGER)",)),))
    newer.migrate()
    with pytest.raises(DatabaseVersionError):
        Database(tmp_path / "a.db").migrate()


def test_transaction_rolls_back_on_error(db):
    with pytest.raises(RuntimeError):
        with db.tx() as c:
            c.execute("INSERT INTO app_settings(key,value) VALUES ('k','v')")
            raise RuntimeError("boom")
    assert db.get_setting("k") is None


def test_foreign_keys_enforced(db):
    with pytest.raises(sqlite3.IntegrityError):
        with db.tx() as c:
            c.execute("INSERT INTO outfit_items(outfit_id, role) VALUES (999, 'top')")


def test_duplicate_outfit_rows_are_impossible(db, basic_wardrobe):
    with db.tx() as c:
        c.execute("INSERT INTO outfits(outfit_date,kind,signature,created_at,updated_at) VALUES ('2026-10-24','wardrobe','w:1-2','x','x')")
    with pytest.raises(sqlite3.IntegrityError):
        with db.tx() as c:
            c.execute("INSERT INTO outfits(outfit_date,kind,signature,created_at,updated_at) VALUES ('2026-10-24','wardrobe','w:1-2','x','x')")
