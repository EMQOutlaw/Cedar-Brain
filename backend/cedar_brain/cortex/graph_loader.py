"""Load and validate a Graphify ``graph.json`` (NetworkX node-link format).

Malformed records are skipped and counted rather than failing the whole
load, so a partly damaged graph is still queryable and the damage is
visible in ``cortex.status``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from ..protocol import BrainError, ErrorCode

MAX_GRAPH_BYTES = 256 * 1024 * 1024
MAX_NODES = 500_000
MAX_LINKS = 2_000_000
CONFIDENCE_DEFAULT_SCORE = {"EXTRACTED": 1.0, "INFERRED": 0.55, "AMBIGUOUS": 0.2}
NODE_FIELDS = ("label", "file_type", "source_file", "source_location", "community", "community_name")


@dataclass
class Node:
    id: str
    label: str
    file_type: str
    source_file: str | None
    source_location: str | None
    community: int | None
    community_name: str | None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Edge:
    source: str
    target: str
    relation: str
    confidence: str
    confidence_score: float
    source_file: str | None

    def to_dict(self) -> dict:
        return {
            "source": self.source, "target": self.target, "relation": self.relation,
            "confidence": self.confidence, "confidence_score": self.confidence_score,
            "evidence": evidence_class(self.confidence), "source_file": self.source_file,
        }


def evidence_class(confidence: str) -> str:
    return {"EXTRACTED": "static_extracted", "INFERRED": "inferred", "AMBIGUOUS": "ambiguous"}.get(confidence, "unknown")


@dataclass
class GraphData:
    path: str
    mtime_ns: int
    size: int
    directed: bool
    schema_version: str | None
    graphify_version: str | None
    built_at_commit: str | None
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    malformed_nodes: int = 0
    malformed_links: int = 0
    dangling_links: int = 0


def _s(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def load_graph(path: str) -> GraphData:
    try:
        st = os.stat(path)
    except FileNotFoundError:
        raise BrainError(ErrorCode.GRAPH_UNAVAILABLE, "graph file not found", {"path": path})
    except OSError as exc:
        raise BrainError(ErrorCode.GRAPH_UNAVAILABLE, f"graph file unreadable: {exc.strerror}", {"path": path})
    if st.st_size > MAX_GRAPH_BYTES:
        raise BrainError(ErrorCode.GRAPH_INVALID, f"graph file exceeds {MAX_GRAPH_BYTES} bytes", {"path": path})
    try:
        with open(path, "rb") as fh:
            raw = json.load(fh)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrainError(ErrorCode.GRAPH_INVALID, f"graph file is not valid JSON: {exc}", {"path": path})
    if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list):
        raise BrainError(ErrorCode.GRAPH_INVALID, "graph has no 'nodes' list (expected Graphify node-link JSON)", {"path": path})
    links = raw.get("links", raw.get("edges", []))
    if not isinstance(links, list):
        raise BrainError(ErrorCode.GRAPH_INVALID, "graph 'links' is not a list", {"path": path})
    if len(raw["nodes"]) > MAX_NODES or len(links) > MAX_LINKS:
        raise BrainError(ErrorCode.GRAPH_INVALID, "graph exceeds supported node/link counts", {"path": path})

    meta = raw.get("graph") if isinstance(raw.get("graph"), dict) else {}
    g = GraphData(
        path=path, mtime_ns=st.st_mtime_ns, size=st.st_size,
        directed=raw.get("directed", True) is not False,
        schema_version=_s(meta.get("schema_version")),
        graphify_version=_s(meta.get("graphify_version")),
        built_at_commit=_s(raw.get("built_at_commit")),
    )
    for item in raw["nodes"]:
        nid = _s(item.get("id")) if isinstance(item, dict) else None
        if not nid or nid in g.nodes:
            g.malformed_nodes += 1
            continue
        community = item.get("community")
        g.nodes[nid] = Node(
            id=nid,
            label=(_s(item.get("label")) or nid)[:500],
            file_type=(_s(item.get("file_type")) or "unknown")[:64],
            source_file=_s(item.get("source_file")),
            source_location=_s(item.get("source_location")),
            community=community if isinstance(community, int) and not isinstance(community, bool) else None,
            community_name=_s(item.get("community_name")),
        )
    for item in links:
        if not isinstance(item, dict):
            g.malformed_links += 1
            continue
        src, tgt = _s(item.get("source")), _s(item.get("target"))
        if not src or not tgt:
            g.malformed_links += 1
            continue
        if src not in g.nodes or tgt not in g.nodes:
            g.dangling_links += 1
            continue
        conf = _s(item.get("confidence")) or "EXTRACTED"
        score = item.get("confidence_score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            score = CONFIDENCE_DEFAULT_SCORE.get(conf, 1.0)
        g.edges.append(Edge(
            source=src, target=tgt,
            relation=(_s(item.get("relation")) or "relates_to").lower()[:64],
            confidence=conf.upper()[:16], confidence_score=float(score),
            source_file=_s(item.get("source_file")),
        ))
    return g
