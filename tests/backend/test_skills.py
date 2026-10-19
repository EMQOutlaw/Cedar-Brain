import os
from pathlib import Path

import pytest

from cedar_brain.app import App
from cedar_brain.protocol import BrainError
from cedar_brain.spores.manifests import ManifestError, read_manifest

from conftest import run


def call(app, method, params=None):
    return run(app.call(method, params or {}))


def test_frontmatter_parser():
    meta, body = read_manifest(b"---\nname: x\ndescription: \"quoted: yes\"\ntags:\n  - a\n  - b\n"
                               b"metadata:\n  version: 2\nnote: |\n  line1\n  line2\n---\nBody\n")
    assert meta == {"name": "x", "description": "quoted: yes", "tags": ["a", "b"], "metadata": {"version": "2"},
                    "note": "line1\nline2"}
    assert body == "Body"
    for bad in (b"---\nname: x\n", b"x" * (300 * 1024), b"\xff\xfe", b"a\x00b"):
        with pytest.raises(ManifestError):
            read_manifest(bad)


def test_discovery_and_inspect(config, skills_dir):
    app = App(config)
    skills = {s["name"]: s for s in call(app, "skills.list")["skills"]}
    assert set(skills) == {"graph-reader", "bare"}
    gr = skills["graph-reader"]
    assert gr["description"] == "Reads graphs carefully." and gr["version"] == "1.2.0"
    assert gr["requirements"] == {"allowed-tools": ["Read", "Grep"]}
    assert gr["trust"] == "untrusted" and gr["has_scripts"] and gr["compatibility"] == "compatible"
    assert skills["bare"]["compatibility"] == "incomplete"
    detail = call(app, "skills.inspect", {"skill_id": gr["id"]})
    assert "Read the graph." in detail["instructions"]
    assert any(f["path"] == "scripts/install.sh" and f.get("script") for f in detail["files"])
    assert "never executed" in detail["notice"]
    assert not Path("/tmp/SHOULD_NEVER_RUN").exists()


def test_symlinks(config, skills_dir, tmp_path):
    outside = Path(os.path.realpath("/usr/share"))
    os.symlink(outside, skills_dir / "evil-link")
    inside = tmp_path / "elsewhere" / "nice"
    inside.mkdir(parents=True)
    (inside / "SKILL.md").write_text("---\nname: nice\ndescription: ok\n---\n")
    os.symlink(inside, skills_dir / "nice-link")  # inside tmp_path, an approved import root
    (skills_dir / "graph-reader" / "leak").symlink_to("/etc/hostname")
    app = App(config)
    res = call(app, "skills.list", {"refresh": True})
    names = {s["name"] for s in res["skills"]}
    assert "nice" in names
    assert any("evil-link" in p["path"] for p in res["problems"])
    gr = next(s for s in res["skills"] if s["name"] == "graph-reader")
    leak = next(f for f in call(app, "skills.inspect", {"skill_id": gr["id"]})["files"] if f["path"] == "leak")
    assert leak["symlink"] and leak["symlink_safe"] is False


def test_symlinked_manifest_rejected(config, skills_dir, tmp_path):
    d = skills_dir / "sneaky"
    d.mkdir()
    (tmp_path / "real.md").write_text("---\nname: s\ndescription: d\n---\n")
    (d / "SKILL.md").symlink_to(tmp_path / "real.md")
    app = App(config)
    s = next(s for s in call(app, "skills.list")["skills"] if s["path"].endswith("sneaky"))
    assert s["compatibility"] == "invalid"


def test_import_local_and_traversal(config, tmp_path):
    app = App(config)
    good = tmp_path / "imports" / "helper"
    good.mkdir(parents=True)
    (good / "SKILL.md").write_text("---\nname: helper\ndescription: helps\n---\nDo things.\n")
    rec = call(app, "skills.import_local", {"path": str(good)})
    assert rec["imported"] and rec["trust"] == "untrusted" and rec["provenance"]["kind"] == "local_import"
    assert any(s["name"] == "helper" for s in call(app, "skills.list", {"refresh": True})["skills"])
    for bad in (str(good) + "/../../../../etc", "/etc", "/usr/share", str(Path.home() / ".ssh")):
        with pytest.raises(BrainError) as e:
            call(app, "skills.import_local", {"path": bad})
        assert e.value.code == "PATH_REJECTED"
    (tmp_path / "nosk").mkdir()
    with pytest.raises(BrainError) as e:
        call(app, "skills.import_local", {"path": str(tmp_path / "nosk")})
    assert e.value.code == "INVALID_PARAMS"


def test_oversized_manifest(config, skills_dir):
    d = skills_dir / "huge"
    d.mkdir()
    (d / "SKILL.md").write_bytes(b"---\nname: huge\ndescription: d\n---\n" + b"x" * (300 * 1024))
    app = App(config)
    s = next(s for s in call(app, "skills.list")["skills"] if s["path"].endswith("huge"))
    assert s["compatibility"] == "invalid"


def test_trust_resets_when_content_changes(config, skills_dir):
    app = App(config)
    s = next(s for s in call(app, "skills.list")["skills"] if s["name"] == "graph-reader")
    app.db.execute("UPDATE skills SET trust='reviewed' WHERE id=?", (s["id"],))
    call(app, "skills.list", {"refresh": True})
    assert app.db.one("SELECT trust FROM skills WHERE id=?", (s["id"],))["trust"] == "reviewed"
    (skills_dir / "graph-reader" / "SKILL.md").write_text("---\nname: graph-reader\ndescription: changed\n---\n")
    call(app, "skills.list", {"refresh": True})
    assert app.db.one("SELECT trust FROM skills WHERE id=?", (s["id"],))["trust"] == "untrusted"
