"""Graph algorithms over the module import graph.

Pure functions on adjacency maps — no storage, no I/O — so every algorithm
is exhaustively testable and reusable by both the analyzer (batch) and the
Neo4j store (which persists the computed properties).

Edge direction convention everywhere: A -> B means "A imports B", i.e.
A depends on B, i.e. B must be migrated before (or together with) A.

SCC detection is iterative Kosaraju, deliberately not recursive Tarjan:
recursion depth would be attacker/input-controlled on a 50k-file legacy
codebase and Python's stack is finite.
"""

from collections import defaultdict
from graphlib import TopologicalSorter

from pydantic import BaseModel, Field

from legacylens.parsing.ast.module_graph import ModuleGraph

Adjacency = dict[str, set[str]]


def build_adjacency(graph: ModuleGraph) -> tuple[Adjacency, Adjacency]:
    """(forward, reverse) internal adjacency, deduplicated."""
    forward: Adjacency = defaultdict(set)
    reverse: Adjacency = defaultdict(set)
    for edge in graph.internal_edges:
        if edge.source == edge.target:
            continue                     # self-import: noise, not structure
        forward[edge.source].add(edge.target)
        reverse[edge.target].add(edge.source)
    for node in graph.files:
        forward.setdefault(node, set())
        reverse.setdefault(node, set())
    return dict(forward), dict(reverse)


def strongly_connected_components(forward: Adjacency) -> list[list[str]]:
    """Iterative Kosaraju. Components returned sorted for determinism."""
    order: list[str] = []
    visited: set[str] = set()
    for start in sorted(forward):
        if start in visited:
            continue
        visited.add(start)
        stack: list[tuple[str, list[str]]] = [
            (start, sorted(forward.get(start, ())))
        ]
        while stack:
            node, targets = stack[-1]
            advanced = False
            while targets:
                nxt = targets.pop()
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append((nxt, sorted(forward.get(nxt, ()))))
                    advanced = True
                    break
            if not advanced:
                order.append(node)
                stack.pop()

    reverse: Adjacency = defaultdict(set)
    for source, targets in forward.items():
        for target in targets:
            reverse[target].add(source)

    visited.clear()
    components: list[list[str]] = []
    for start in reversed(order):
        if start in visited:
            continue
        component, frontier = [], [start]
        visited.add(start)
        while frontier:
            node = frontier.pop()
            component.append(node)
            for nxt in reverse.get(node, ()):
                if nxt not in visited:
                    visited.add(nxt)
                    frontier.append(nxt)
        components.append(sorted(component))
    return components


class ModuleMetrics(BaseModel):
    path: str
    fan_in: int = 0          # distinct internal importers
    fan_out: int = 0         # distinct internal imports
    wave: int = 0            # migration wave index (0 = migrate first)
    cycle_id: int | None = None   # set when part of a multi-node cycle


class ArchitectureReport(BaseModel):
    """Artifact published by the architecture analyzer."""

    cycles: list[list[str]] = Field(default_factory=list)
    waves: list[list[str]] = Field(default_factory=list)
    metrics: dict[str, ModuleMetrics] = Field(default_factory=dict)

    @property
    def parallelizable_wave_sizes(self) -> list[int]:
        return [len(w) for w in self.waves]


def analyze_architecture(graph: ModuleGraph) -> ArchitectureReport:
    forward, reverse = build_adjacency(graph)
    components = strongly_connected_components(forward)

    cycles = [c for c in components if len(c) > 1]
    component_of: dict[str, int] = {}
    for idx, component in enumerate(components):
        for node in component:
            component_of[node] = idx

    # Condensation: edges between components (a DAG by construction).
    comp_deps: dict[int, set[int]] = defaultdict(set)
    for source, targets in forward.items():
        for target in targets:
            a, b = component_of[source], component_of[target]
            if a != b:
                comp_deps[a].add(b)     # component a depends on component b

    sorter: TopologicalSorter[int] = TopologicalSorter()
    for idx in range(len(components)):
        sorter.add(idx, *comp_deps.get(idx, ()))
    depth: dict[int, int] = {}
    for idx in sorter.static_order():   # dependencies come first
        deps = comp_deps.get(idx, ())
        depth[idx] = 1 + max((depth[d] for d in deps), default=-1)

    max_depth = max(depth.values(), default=-1)
    waves: list[list[str]] = [[] for _ in range(max_depth + 1)]
    for idx, component in enumerate(components):
        waves[depth[idx]].extend(component)
    for wave in waves:
        wave.sort()

    cycle_ids = {tuple(c): i for i, c in enumerate(cycles)}
    metrics = {
        node: ModuleMetrics(
            path=node,
            fan_in=len(reverse.get(node, ())),
            fan_out=len(forward.get(node, ())),
            wave=depth[component_of[node]],
            cycle_id=cycle_ids.get(
                tuple(components[component_of[node]])
            ) if len(components[component_of[node]]) > 1 else None,
        )
        for node in forward
    }
    return ArchitectureReport(cycles=cycles, waves=waves, metrics=metrics)


def blast_radius(graph: ModuleGraph, path: str) -> set[str]:
    """Every module that transitively imports `path` — what breaks if it
    changes. The single most-asked question in migration planning."""
    _, reverse = build_adjacency(graph)
    seen: set[str] = set()
    frontier = [path]
    while frontier:
        node = frontier.pop()
        for importer in reverse.get(node, ()):
            if importer not in seen:
                seen.add(importer)
                frontier.append(importer)
    seen.discard(path)
    return seen
