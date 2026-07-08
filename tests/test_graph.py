"""Graph layer tests: algorithm correctness first (with ordering
invariants asserted, not just examples), then the analyzer, then the
Neo4j store against a recording stub driver."""

from pathlib import Path

import pytest

from legacylens.analyzers.architecture import ArchitectureAnalyzer
from legacylens.core.config import Settings
from legacylens.domain.models import ProjectContext
from legacylens.graph.algorithms import (
    analyze_architecture,
    blast_radius,
    strongly_connected_components,
)
from legacylens.graph.store import Neo4jGraphStore
from legacylens.parsing.ast.module_graph import ImportEdge, ModuleGraph


def make_graph(edges: list[tuple[str, str]],
               extra_files: list[str] = ()) -> ModuleGraph:
    files = sorted({n for e in edges for n in e} | set(extra_files))
    return ModuleGraph(
        files=files,
        edges=[
            ImportEdge(source=s, target=t, raw=t, line=1, internal=True)
            for s, t in edges
        ],
    )


class TestScc:
    def test_finds_multiple_cycles(self):
        forward = {
            "a": {"b"}, "b": {"c"}, "c": {"a"},        # 3-cycle
            "x": {"y"}, "y": {"x"},                     # 2-cycle
            "solo": {"a"},
        }
        components = strongly_connected_components(forward)
        multi = sorted(c for c in components if len(c) > 1)
        assert multi == [["a", "b", "c"], ["x", "y"]]
        assert ["solo"] in components

    def test_dag_has_only_singletons(self):
        forward = {"a": {"b", "c"}, "b": {"c"}, "c": set()}
        assert all(len(c) == 1
                   for c in strongly_connected_components(forward))

    def test_deep_chain_does_not_recurse(self):
        # 20k-node chain would explode a recursive implementation.
        forward = {f"n{i}": {f"n{i+1}"} for i in range(20_000)}
        forward["n20000"] = set()
        components = strongly_connected_components(forward)
        assert len(components) == 20_001


class TestWaves:
    def test_dependencies_always_in_earlier_waves(self):
        graph = make_graph([
            ("app", "service"), ("app", "util"),
            ("service", "db"), ("service", "util"),
            ("db", "util"),
        ])
        report = analyze_architecture(graph)
        wave_of = {p: m.wave for p, m in report.metrics.items()}

        assert wave_of["util"] == 0
        assert wave_of["db"] == 1
        assert wave_of["service"] == 2
        assert wave_of["app"] == 3
        # invariant: every dependency sits in a strictly earlier wave
        for edge in graph.internal_edges:
            assert wave_of[edge.target] < wave_of[edge.source]

    def test_cycle_moves_as_one_unit(self):
        graph = make_graph([
            ("a", "b"), ("b", "a"),      # cycle
            ("a", "base"), ("top", "a"),
        ])
        report = analyze_architecture(graph)
        wave_of = {p: m.wave for p, m in report.metrics.items()}

        assert report.cycles == [["a", "b"]]
        assert wave_of["a"] == wave_of["b"]           # one unit
        assert wave_of["base"] < wave_of["a"] < wave_of["top"]
        assert report.metrics["a"].cycle_id == report.metrics["b"].cycle_id
        assert report.metrics["base"].cycle_id is None

    def test_parallel_opportunities_share_a_wave(self):
        graph = make_graph([("a", "base"), ("b", "base"), ("c", "base")])
        report = analyze_architecture(graph)
        assert report.waves[0] == ["base"]
        assert report.waves[1] == ["a", "b", "c"]     # parallelizable

    def test_isolated_file_is_wave_zero(self):
        graph = make_graph([("a", "b")], extra_files=["lonely.py"])
        report = analyze_architecture(graph)
        assert report.metrics["lonely.py"].wave == 0


class TestBlastRadius:
    def test_transitive_importers_only(self):
        graph = make_graph([
            ("app", "service"), ("service", "db"),
            ("cli", "service"), ("db", "driver"),
        ])
        assert blast_radius(graph, "db") == {"app", "service", "cli"}
        assert blast_radius(graph, "driver") == {"db", "service", "app",
                                                 "cli"}
        assert blast_radius(graph, "app") == set()


class TestArchitectureAnalyzer:
    def make_ctx(self, tmp_path: Path, graph: ModuleGraph) -> ProjectContext:
        ctx = ProjectContext(project_id="p1", root=tmp_path)
        ctx.artifacts["module_graph"] = graph
        return ctx

    def test_cycle_finding_with_edge_evidence(self, tmp_path):
        graph = make_graph([("a", "b"), ("b", "a"), ("a", "base")])
        result = ArchitectureAnalyzer().analyze(self.make_ctx(tmp_path, graph))

        cycle = next(f for f in result.findings
                     if f.rule_id == "ARCH-CYCLE-001")
        assert cycle.metadata["members"] == ["a", "b"]
        assert {(e.file_path, e.detail) for e in cycle.evidence} == {
            ("a", "imports b"), ("b", "imports a"),
        }
        waves = next(f for f in result.findings
                     if f.rule_id == "ARCH-WAVES-001")
        assert waves.metadata["wave_sizes"] == [1, 2]

    def test_hotspot_threshold(self, tmp_path):
        edges = [(f"consumer{i}", "core") for i in range(12)]
        graph = make_graph(edges)
        result = ArchitectureAnalyzer().analyze(self.make_ctx(tmp_path, graph))

        hotspot = next(f for f in result.findings
                       if f.rule_id == "ARCH-HOTSPOT-001")
        assert hotspot.metadata["hotspots"][0] == {
            "path": "core", "fan_in": 12, "fan_out": 0,
        }

    def test_quiet_on_small_clean_graph(self, tmp_path):
        graph = make_graph([("a", "b")])
        result = ArchitectureAnalyzer().analyze(self.make_ctx(tmp_path, graph))
        assert [f.rule_id for f in result.findings] == ["ARCH-WAVES-001"]


class RecordingSession:
    def __init__(self, log):
        self._log = log

    def run(self, statement, **params):
        self._log.append((statement, params))
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class StubDriver:
    def __init__(self):
        self.log = []

    def session(self, database=None):
        return RecordingSession(self.log)

    def close(self):
        pass


class TestNeo4jStore:
    def make_store(self):
        driver = StubDriver()
        settings = Settings(_env_file=None)
        return Neo4jGraphStore(settings, driver=driver), driver

    def test_load_batches_and_parameters(self):
        store, driver = self.make_store()
        graph = make_graph([("a", "b"), ("a", "b"), ("b", "c")])  # dup edge
        report = analyze_architecture(graph)

        store.ensure_schema()
        store.load("proj-1", graph, report)

        statements = [s for s, _ in driver.log]
        assert any("CREATE CONSTRAINT" in s for s in statements)
        node_calls = [(s, p) for s, p in driver.log if "MERGE (m:Module" in s]
        edge_calls = [(s, p) for s, p in driver.log if ":IMPORTS" in s]

        assert len(node_calls) == 1 and len(edge_calls) == 1
        _, node_params = node_calls[0]
        assert node_params["project_id"] == "proj-1"
        assert {r["path"] for r in node_params["rows"]} == {"a", "b", "c"}
        assert all("wave" in r and "fan_in" in r
                   for r in node_params["rows"])

        _, edge_params = edge_calls[0]
        assert len(edge_params["rows"]) == 2      # duplicate deduped

    def test_large_graph_is_chunked(self):
        store, driver = self.make_store()
        edges = [(f"m{i}", "core") for i in range(1500)]
        graph = make_graph(edges)
        report = analyze_architecture(graph)
        store.load("proj-1", graph, report)

        node_calls = [p for s, p in driver.log if "MERGE (m:Module" in s]
        assert len(node_calls) == 2               # 1501 nodes -> 2 batches
        assert sum(len(p["rows"]) for p in node_calls) == 1501
