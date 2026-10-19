"""Deterministic graph queries. All traversals are bounded.

Every relationship returned here is a *static* relationship that Graphify
extracted or inferred from source. None of it proves a runtime dependency;
responses say so explicitly via ``relationship_kind`` and ``evidence``.
"""

from __future__ import annotations

from collections import deque

from ..protocol import BrainError, ErrorCode
from .graph_index import GraphIndex

MAX_DEPTH = 4
MAX_RESULTS = 500
MAX_VISITED = 20_000
STATIC_NOTE = ("Relationships are static code relationships from the Graphify graph; "
               "they do not prove runtime dependencies. 'inferred' and 'ambiguous' edges are suggestions.")

# Relations along which a change propagates to the *source* of the edge
# (A imports B → changing B may affect A).
DEPENDENCY_RELATIONS = frozenset({
    "imports", "imports_from", "calls", "indirect_call", "references", "re_exports", "includes",
    "inherits", "extends", "implements", "uses", "instantiates", "depends_on",
})
CONFIDENCE_RANK = {"AMBIGUOUS": 0, "INFERRED": 1, "EXTRACTED": 2}


def _node_or_404(idx: GraphIndex, node_id: str):
    node = idx.data.nodes.get(node_id)
    if node is None:
        raise BrainError(ErrorCode.NOT_FOUND, f"no graph node {node_id!r}")
    return node


def search(idx: GraphIndex, query: str, field: str = "any", file_type: str | None = None, limit: int = 25) -> list[dict]:
    q = query.strip().lower()
    if not q:
        return []
    ft = file_type.lower() if file_type else None
    scored: list[tuple[int, str, str]] = []
    for nid, id_l, label_l, file_l, type_l in idx.search_keys:
        if ft and type_l != ft:
            continue
        score = 0
        if field in ("any", "id"):
            if id_l == q:
                score = 100
            elif q in id_l:
                score = max(score, 30)
        if field in ("any", "name"):
            if label_l == q:
                score = max(score, 90)
            elif label_l.startswith(q):
                score = max(score, 70)
            elif q in label_l:
                score = max(score, 50)
        if field in ("any", "path") and file_l:
            if file_l == q or file_l.endswith("/" + q):
                score = max(score, 80)
            elif q in file_l:
                score = max(score, 40)
        if field == "type" and type_l == q:
            score = 60
        if score:
            scored.append((-score, label_l, nid))
    scored.sort()
    out = []
    for neg, _, nid in scored[:limit]:
        d = idx.data.nodes[nid].to_dict()
        d["score"] = -neg
        out.append(d)
    return out


def _edge_ok(e, relations: frozenset | None, min_rank: int) -> bool:
    if relations is not None and e.relation not in relations:
        return False
    return CONFIDENCE_RANK.get(e.confidence, 0) >= min_rank


def traverse(idx: GraphIndex, node_id: str, direction: str = "out", depth: int = 1,
             relations: list[str] | None = None, min_confidence: str = "AMBIGUOUS",
             limit: int = 100) -> dict:
    """Breadth-first walk. direction: out = what node depends on, in = what depends on node."""
    _node_or_404(idx, node_id)
    depth = max(1, min(depth, MAX_DEPTH))
    limit = max(1, min(limit, MAX_RESULTS))
    rels = frozenset(r.lower() for r in relations) if relations else None
    min_rank = CONFIDENCE_RANK.get(min_confidence.upper(), 0)
    edges = idx.data.edges
    seen = {node_id: 0}
    results: list[dict] = []
    used_edges: list[dict] = []
    truncated = False
    queue = deque([node_id])
    while queue:
        cur = queue.popleft()
        dist = seen[cur]
        if dist >= depth:
            continue
        steps = []
        if direction in ("out", "both"):
            steps += [(edges[i], edges[i].target) for i in idx.out_edges.get(cur, ())]
        if direction in ("in", "both"):
            steps += [(edges[i], edges[i].source) for i in idx.in_edges.get(cur, ())]
        for e, nxt in steps:
            if not _edge_ok(e, rels, min_rank):
                continue
            if nxt in seen:
                continue
            if len(results) >= limit or len(seen) >= MAX_VISITED:
                truncated = True
                break
            seen[nxt] = dist + 1
            d = idx.data.nodes[nxt].to_dict()
            d["distance"] = dist + 1
            d["via"] = {"from": cur, "relation": e.relation, "evidence": e.to_dict()["evidence"]}
            results.append(d)
            used_edges.append(e.to_dict())
            queue.append(nxt)
        if truncated:
            break
    return {
        "root": node_id, "direction": direction, "depth": depth,
        "nodes": results, "edges": used_edges, "truncated": truncated,
        "relationship_kind": "static", "note": STATIC_NOTE,
    }


def impact(idx: GraphIndex, node_ids: list[str], depth: int = 2, limit: int = 100,
           min_confidence: str = "AMBIGUOUS") -> dict:
    """Components that may be affected by changing ``node_ids``.

    Walks dependency relations backwards (who imports/calls/references the
    target) and ``contains`` upwards (the file/module holding a symbol).
    """
    for nid in node_ids:
        _node_or_404(idx, nid)
    depth = max(1, min(depth, MAX_DEPTH))
    limit = max(1, min(limit, MAX_RESULTS))
    min_rank = CONFIDENCE_RANK.get(min_confidence.upper(), 0)
    edges = idx.data.edges
    seen = {n: 0 for n in node_ids}
    weakest: dict[str, int] = {n: 2 for n in node_ids}  # weakest confidence along the path
    results: list[dict] = []
    truncated = False
    queue = deque(node_ids)
    while queue and not truncated:
        cur = queue.popleft()
        if seen[cur] >= depth:
            continue
        for i in idx.in_edges.get(cur, ()):
            e = edges[i]
            if e.relation == "contains":
                continue  # the container is handled below
            if e.relation not in DEPENDENCY_RELATIONS or CONFIDENCE_RANK.get(e.confidence, 0) < min_rank:
                continue
            truncated = _visit(idx, e, e.source, cur, seen, weakest, results, queue, limit)
            if truncated:
                break
        if truncated:
            break
        for i in idx.in_edges.get(cur, ()):
            e = edges[i]
            if e.relation == "contains" and CONFIDENCE_RANK.get(e.confidence, 0) >= min_rank:
                truncated = _visit(idx, e, e.source, cur, seen, weakest, results, queue, limit)
                if truncated:
                    break
    rank_name = {2: "static_extracted", 1: "inferred", 0: "ambiguous"}
    for r in results:
        r["path_evidence"] = rank_name[r.pop("_rank")]
    return {
        "targets": node_ids, "depth": depth, "impacted": results, "truncated": truncated,
        "files": sorted({r["source_file"] for r in results if r.get("source_file")}),
        "relationship_kind": "static",
        "note": STATIC_NOTE + " Impact is a candidate list for review, not a proof of breakage.",
    }


def _visit(idx, e, nxt, cur, seen, weakest, results, queue, limit) -> bool:
    if nxt in seen:
        return False
    if len(results) >= limit or len(seen) >= MAX_VISITED:
        return True
    seen[nxt] = seen[cur] + 1
    weakest[nxt] = min(weakest[cur], CONFIDENCE_RANK.get(e.confidence, 0))
    d = idx.data.nodes[nxt].to_dict()
    d["distance"] = seen[nxt]
    d["via"] = {"to": cur, "relation": e.relation, "evidence": e.to_dict()["evidence"]}
    d["_rank"] = weakest[nxt]
    results.append(d)
    queue.append(nxt)
    return False


def node_detail(idx: GraphIndex, node_id: str, edge_limit: int = 100) -> dict:
    node = _node_or_404(idx, node_id)
    edges = idx.data.edges
    out_e = [edges[i].to_dict() for i in idx.out_edges.get(node_id, ())[:edge_limit]]
    in_e = [edges[i].to_dict() for i in idx.in_edges.get(node_id, ())[:edge_limit]]
    return {
        "node": node.to_dict(),
        "outgoing": out_e, "incoming": in_e,
        "outgoing_total": len(idx.out_edges.get(node_id, ())),
        "incoming_total": len(idx.in_edges.get(node_id, ())),
        "relationship_kind": "static",
    }


def communities(idx: GraphIndex, community_id: int | None = None, limit: int = 50) -> dict:
    if community_id is not None:
        members = idx.communities.get(community_id)
        if members is None:
            raise BrainError(ErrorCode.NOT_FOUND, f"no community {community_id}")
        nodes = [idx.data.nodes[m].to_dict() for m in members[:limit]]
        name = next((n["community_name"] for n in nodes if n.get("community_name")), None)
        return {"community": {"id": community_id, "name": name, "size": len(members)},
                "members": nodes, "truncated": len(members) > limit}
    ranked = sorted(idx.communities.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    out = []
    for cid, members in ranked[:limit]:
        name = next((idx.data.nodes[m].community_name for m in members if idx.data.nodes[m].community_name), None)
        sample = [idx.data.nodes[m].label for m in members[:5]]
        out.append({"id": cid, "name": name, "size": len(members), "sample": sample})
    return {"communities": out, "total": len(idx.communities), "truncated": len(ranked) > limit}
