import os
import sqlite3
import stat
import time

import pytest

from cedar_brain.security.paths import ensure_within, resolve_in_project
from cedar_brain.security.redaction import redact, redact_obj
from cedar_brain.storage import migrations
from cedar_brain.storage.database import Database, SchemaTooNew
from cedar_brain.protocol import BrainError


def test_migrate_fresh_db(tmp_path):
    db = Database(tmp_path / "s" / "brain.db")
    assert db.migrate() == [migrations.SCHEMA_VERSION]
    assert db.schema_version == migrations.SCHEMA_VERSION
    assert db.migrate() == []
    assert stat.S_IMODE(os.stat(tmp_path / "s" / "brain.db").st_mode) == 0o600
    tables = {r[0] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"projects", "skills", "plans", "workflows", "runs", "run_steps", "run_events", "agent_jobs",
            "verifications", "preferences"} <= tables


def test_backup_before_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "brain.db"
    db = Database(path)
    db.migrate()
    db.set_pref("k", {"a": 1})
    db.close()
    monkeypatch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS + [(99, "CREATE TABLE extra(x INTEGER)")])
    monkeypatch.setattr("cedar_brain.storage.database.MIGRATIONS", migrations.MIGRATIONS)
    monkeypatch.setattr("cedar_brain.storage.database.SCHEMA_VERSION", 99)
    db = Database(path)
    assert db.migrate() == [99]
    backups = list(tmp_path.glob("brain.db.bak-v1-*"))
    assert len(backups) == 1 and stat.S_IMODE(os.stat(backups[0]).st_mode) == 0o600
    old = sqlite3.connect(backups[0])
    assert old.execute("PRAGMA user_version").fetchone()[0] == 1


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "brain.db"
    Database(path).migrate()
    monkeypatch.setattr("cedar_brain.storage.database.MIGRATIONS",
                        migrations.MIGRATIONS + [(2, "CREATE TABLE ok_t(x INTEGER); THIS IS NOT SQL")])
    monkeypatch.setattr("cedar_brain.storage.database.SCHEMA_VERSION", 2)
    db = Database(path)
    with pytest.raises(sqlite3.Error):
        db.migrate()
    assert db.schema_version == 1
    assert not db.one("SELECT 1 FROM sqlite_master WHERE name='ok_t'")


def test_newer_schema_refused(tmp_path):
    db = Database(tmp_path / "brain.db")
    db.execute("PRAGMA user_version = 999")
    with pytest.raises(SchemaTooNew):
        db.migrate()


def test_no_credentials_in_schema():
    sql = " ".join(s for _, s in migrations.MIGRATIONS).lower()
    for word in ("token", "password", "api_key", "secret", "credential"):
        assert word not in sql


@pytest.mark.parametrize("secret", [
    "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA", "sk-proj-abcdefghijklmnopqrstuvwxyz012345",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789", "github_pat_11ABCDEFG0123456789_abcdefghijklmnop",
    "xoxb-1234567890-abcdefghij", "AKIAABCDEFGHIJKLMNOP",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
])
def test_redacts_token_shapes(secret):
    assert secret not in redact(f"value: {secret} end")


def test_redacts_key_values_and_structures():
    assert redact('password="hunter2hunter2"') == 'password="[REDACTED]"'
    assert redact("Authorization: Bearer abc.def.ghijklmnopqrstu") == "Authorization: Bearer [REDACTED]"
    assert redact_obj({"a": ["api_key=abcdef123456"], "n": 3}) == {"a": ["api_key=[REDACTED]"], "n": 3}
    assert redact("-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----") == "[REDACTED]"
    assert redact("ordinary text with tokens of meaning") == "ordinary text with tokens of meaning"


def test_redaction_is_linear():
    t0 = time.monotonic()
    redact("x" * 1_000_000)
    redact("a_" * 500_000)
    assert time.monotonic() - t0 < 2


def test_path_helpers(tmp_path):
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    os.symlink("/etc", root / "a" / "etc-link")
    assert resolve_in_project(str(root), "a") == root / "a"
    assert resolve_in_project(str(root), "../x") is None
    assert resolve_in_project(str(root), "a/etc-link/passwd") is None
    assert resolve_in_project(str(root), "/etc/passwd") is None
    assert ensure_within(root / "a", [tmp_path]) == root / "a"
    for bad in (root / "a" / "etc-link", "/proc/self", str(tmp_path) + "/../.."):
        with pytest.raises(BrainError):
            ensure_within(bad, [tmp_path])
