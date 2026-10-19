import json

import pytest

from cedar_brain.app import App
from cedar_brain.protocol import BrainError

from conftest import git, run


def call(app, method, params=None):
    return run(app.call(method, params or {}))


def test_register_select_status(config, repo, tmp_path):
    app = App(config)
    p = call(app, "projects.add", {"path": str(repo / "src")})  # subdir → normalized to top level
    assert p["root"] == str(repo) and p["name"] == "proj"
    assert call(app, "projects.list")["active_project_id"] == p["id"]
    st = call(app, "projects.status")
    assert st["git"]["branch"] == "main" and st["git"]["clean"] is True
    assert st["graph"]["state"] == "present" and st["capabilities"]["cortex"]
    (repo / "src" / "new.txt").write_text("x")
    assert call(app, "projects.status")["git"]["changed_files"] == 1
    with pytest.raises(BrainError) as e:
        call(app, "projects.add", {"path": str(repo)})
    assert e.value.code == "CONFLICT"


def test_invalid_repositories(config, tmp_path):
    app = App(config)
    plain = tmp_path / "plain"
    plain.mkdir()
    for path, code in ((plain, "INVALID_REPOSITORY"), (tmp_path / "missing", "INVALID_REPOSITORY")):
        with pytest.raises(BrainError) as e:
            call(app, "projects.add", {"path": str(path)})
        assert e.value.code == code
    assert not (plain / ".git").exists()  # never initialised
    with pytest.raises(BrainError) as e:
        call(app, "projects.status")
    assert e.value.code == "NO_ACTIVE_PROJECT"


def test_missing_repo_detected(config, repo):
    app = App(config)
    call(app, "projects.add", {"path": str(repo)})
    import shutil
    shutil.rmtree(repo)
    st = call(app, "projects.status")
    assert st["git"]["valid"] is False and "missing" in st["git"]["error"]


def test_multiple_projects(config, repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    app = App(config)
    a = call(app, "projects.add", {"path": str(repo)})
    b = call(app, "projects.add", {"path": str(other), "select": True})
    assert call(app, "projects.list")["active_project_id"] == b["id"]
    call(app, "projects.select", {"project_id": a["id"]})
    assert call(app, "projects.list")["active_project_id"] == a["id"]
    st = call(app, "projects.status", {"project_id": b["id"]})
    assert st["git"]["head"] is None and st["capabilities"]["worktrees"] is False  # unborn branch


# -- cortex ------------------------------------------------------------------------

@pytest.fixture
def capp(config, repo):
    app = App(config)
    call(app, "projects.add", {"path": str(repo)})
    return app


def test_graph_load_and_status(capp):
    st = call(capp, "cortex.status")
    assert st["loaded"] and st["nodes"] == 8 and st["edges"] == 6
    assert st["malformed"] == {"nodes": 1, "links": 1, "dangling_links": 1}
    assert st["freshness"]["state"] == "fresh" and st["freshness"]["changed_on_disk"] is False


def test_search(capp):
    r = call(capp, "cortex.search", {"query": "StrataGeometry.js"})["results"]
    assert r[0]["id"] == "strata_geometry"
    r = call(capp, "cortex.search", {"query": "src/Bar.qml", "field": "path"})["results"]
    assert [n["id"] for n in r] == ["bar"]
    r = call(capp, "cortex.search", {"query": "doc_ref", "field": "type"})["results"]
    assert [n["id"] for n in r] == ["design_doc"]
    assert call(capp, "cortex.search", {"query": "zzz"})["results"] == []


def test_dependencies_both_directions(capp):
    # "What imports StrataGeometry.js?" — reverse traversal
    r = call(capp, "cortex.dependencies", {"node_id": "strata_geometry", "direction": "in", "depth": 2,
                                           "relations": ["imports"]})
    assert {n["id"]: n["distance"] for n in r["nodes"]} == {"canopy": 1, "bar": 2}
    assert r["relationship_kind"] == "static" and "runtime" in r["note"]
    out = call(capp, "cortex.dependencies", {"node_id": "bar", "direction": "out", "depth": 1})
    assert {n["id"] for n in out["nodes"]} == {"canopy", "notifications"}
    ev = {e["target"]: e["evidence"] for e in out["edges"]}
    assert ev["notifications"] == "ambiguous" and ev["canopy"] == "static_extracted"
    strict = call(capp, "cortex.dependencies", {"node_id": "bar", "min_confidence": "EXTRACTED"})
    assert {n["id"] for n in strict["nodes"]} == {"canopy"}


def test_traversal_bounds(capp):
    r = call(capp, "cortex.dependencies", {"node_id": "strata_geometry", "direction": "both", "depth": 4, "limit": 1})
    assert len(r["nodes"]) == 1 and r["truncated"]
    with pytest.raises(BrainError) as e:
        call(capp, "cortex.dependencies", {"node_id": "strata_geometry", "depth": 9})
    assert e.value.code == "INVALID_PARAMS"
    with pytest.raises(BrainError) as e:
        call(capp, "cortex.dependencies", {"node_id": "nope"})
    assert e.value.code == "NOT_FOUND"


def test_impact(capp):
    r = call(capp, "cortex.impact", {"node_ids": ["strata_geometry.build"], "depth": 3})
    ids = {n["id"]: n for n in r["impacted"]}
    # build() is contained by StrataGeometry.js, which Canopy imports, which Bar imports
    assert set(ids) == {"strata_geometry", "canopy", "bar"}
    assert ids["bar"]["path_evidence"] == "static_extracted"
    assert "src/Bar.qml" in r["files"] and "not a proof" in r["note"]
    r = call(capp, "cortex.impact", {"path": "src/NotificationService.qml"})
    by = {n["id"]: n["path_evidence"] for n in r["impacted"]}
    assert by == {"whispers": "inferred", "bar": "ambiguous"}
    with pytest.raises(BrainError):
        call(capp, "cortex.impact", {})


def test_node_and_source_resolution(capp, repo):
    n = call(capp, "cortex.node", {"node_id": "canopy"})
    assert n["source"]["exists"] and n["source"]["path"] == str(repo / "src" / "Canopy.qml") and n["source"]["line"] == 1
    assert {e["source"] for e in n["incoming"]} == {"bar", "design_doc"}
    esc = call(capp, "cortex.node", {"node_id": "escape"})
    assert esc["source"]["within_project"] is False and esc["source"]["path"] is None


def test_communities(capp):
    r = call(capp, "cortex.communities")
    assert r["communities"][0]["size"] == 3 and r["total"] == 3
    one = call(capp, "cortex.communities", {"community_id": 1})
    assert one["community"]["name"] == "Shell" and len(one["members"]) == 3


def test_stale_and_changed_graph(capp, repo):
    call(capp, "cortex.status")
    (repo / "src" / "Bar.qml").write_text("// changed\n")
    git(repo, "commit", "-qam", "change")
    gp = repo / "graphify-out" / "graph.json"
    data = json.loads(gp.read_text())
    data["nodes"].append({"id": "extra", "label": "Extra"})
    gp.write_text(json.dumps(data))
    st = call(capp, "cortex.status")
    assert st["freshness"]["state"] == "stale" and st["freshness"]["commits_behind"] == 1
    assert st["freshness"]["changed_on_disk"] is True and st["nodes"] == 8  # not silently reloaded
    st = call(capp, "cortex.reload")
    assert st["nodes"] == 9 and st["freshness"]["changed_on_disk"] is False


def test_graph_unavailable_and_invalid(config, repo):
    app = App(config)
    call(app, "projects.add", {"path": str(repo)})
    gp = repo / "graphify-out" / "graph.json"
    gp.write_text("{not json")
    st = call(app, "cortex.status")
    assert st["state"] == "invalid" and st["error"]["code"] == "GRAPH_INVALID"
    gp.unlink()
    app.cortex.forget(app.projects.active_id)
    assert call(app, "cortex.status")["state"] == "unavailable"
    with pytest.raises(BrainError) as e:
        call(app, "cortex.search", {"query": "x"})
    assert e.value.code == "GRAPH_UNAVAILABLE"
    assert call(app, "projects.status")["git"]["valid"]  # rest of the app still works


def test_graph_without_commit_metadata(config, repo):
    from conftest import sample_graph
    (repo / "graphify-out" / "graph.json").write_text(json.dumps(sample_graph(None)))
    app = App(config)
    call(app, "projects.add", {"path": str(repo)})
    assert call(app, "cortex.status")["freshness"]["state"] == "unknown"


def test_index_built_once(capp, monkeypatch):
    import cedar_brain.cortex.service as svc
    calls = []
    real = svc.load_graph
    monkeypatch.setattr(svc, "load_graph", lambda p: calls.append(p) or real(p))
    for _ in range(5):
        call(capp, "cortex.search", {"query": "bar"})
    assert len(calls) == 1
