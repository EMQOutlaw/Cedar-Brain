"""In-memory index over a loaded graph. Built once per load, never per request."""

from __future__ import annotations

from collections import defaultdict

from .graph_loader import GraphData


class GraphIndex:
    def __init__(self, data: GraphData):
        self.data = data
        self.out_edges: dict[str, list[int]] = defaultdict(list)
        self.in_edges: dict[str, list[int]] = defaultdict(list)
        self.by_file: dict[str, list[str]] = defaultdict(list)
        self.communities: dict[int, list[str]] = defaultdict(list)
        self.relations: dict[str, int] = defaultdict(int)
        # Pre-lowered search keys: (node_id, id_lower, label_lower, file_lower, type_lower)
        self.search_keys: list[tuple[str, str, str, str, str]] = []
        for i, e in enumerate(data.edges):
            self.out_edges[e.source].append(i)
            self.in_edges[e.target].append(i)
            self.relations[e.relation] += 1
        for n in data.nodes.values():
            if n.source_file:
                self.by_file[n.source_file].append(n.id)
            if n.community is not None:
                self.communities[n.community].append(n.id)
            self.search_keys.append((n.id, n.id.lower(), n.label.lower(), (n.source_file or "").lower(), n.file_type.lower()))

    @property
    def node_count(self) -> int:
        return len(self.data.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.data.edges)
