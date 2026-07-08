"""Neo4j persistence for the module graph.

Deliberately thin: all analysis is computed in Python (algorithms.py) and
persisted here as node properties, so the dashboard's Architecture
Explorer can answer interactive questions (blast radius, cycle membership,
wave filtering) with simple Cypher instead of recomputation.

The driver is injectable: unit tests run against a recording stub; real
integration runs against the Docker Compose Neo4j in Module 14. The neo4j
package is an optional extra (pip install legacylens[graph]) — the core
pipeline must not require a database.

Write pattern: batched UNWIND + MERGE, idempotent per (project_id, path),
so re-analyzing a project updates in place instead of duplicating.
"""

from typing import Any, Iterable

from legacylens.core.config import Settings
from legacylens.graph.algorithms import ArchitectureReport
from legacylens.parsing.ast.module_graph import ModuleGraph

BATCH_SIZE = 1000

SCHEMA_STATEMENTS = (
    "CREATE CONSTRAINT module_key IF NOT EXISTS "
    "FOR (m:Module) REQUIRE (m.project_id, m.path) IS NODE KEY",
)

NODE_STATEMENT = (
    "UNWIND $rows AS row "
    "MERGE (m:Module {project_id: $project_id, path: row.path}) "
    "SET m.fan_in = row.fan_in, m.fan_out = row.fan_out, "
    "    m.wave = row.wave, m.cycle_id = row.cycle_id"
)

EDGE_STATEMENT = (
    "UNWIND $rows AS row "
    "MATCH (s:Module {project_id: $project_id, path: row.source}) "
    "MATCH (t:Module {project_id: $project_id, path: row.target}) "
    "MERGE (s)-[r:IMPORTS]->(t) "
    "SET r.line = row.line"
)

BLAST_RADIUS_QUERY = (
    "MATCH (m:Module {project_id: $project_id, path: $path})"
    "<-[:IMPORTS*1..]-(up:Module) "
    "RETURN DISTINCT up.path AS path"
)

CYCLE_MEMBERS_QUERY = (
    "MATCH (m:Module {project_id: $project_id}) "
    "WHERE m.cycle_id IS NOT NULL "
    "RETURN m.cycle_id AS cycle_id, collect(m.path) AS members "
    "ORDER BY cycle_id"
)


def _chunks(rows: list[dict[str, Any]],
            size: int = BATCH_SIZE) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


class Neo4jGraphStore:
    def __init__(self, settings: Settings, driver: Any | None = None) -> None:
        if driver is None:
            try:
                from neo4j import GraphDatabase
            except ImportError as exc:   # pragma: no cover
                raise ImportError(
                    "Neo4j persistence requires the optional extra: "
                    "pip install 'legacylens[graph]'"
                ) from exc
            driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
        self._driver = driver
        self._database = settings.neo4j_database

    def ensure_schema(self) -> None:
        with self._driver.session(database=self._database) as session:
            for statement in SCHEMA_STATEMENTS:
                session.run(statement)

    def load(self, project_id: str, graph: ModuleGraph,
             report: ArchitectureReport) -> None:
        node_rows = [
            {
                "path": m.path,
                "fan_in": m.fan_in,
                "fan_out": m.fan_out,
                "wave": m.wave,
                "cycle_id": m.cycle_id,
            }
            for m in report.metrics.values()
        ]
        seen: set[tuple[str, str]] = set()
        edge_rows = []
        for edge in graph.internal_edges:
            key = (edge.source, edge.target)
            if key in seen or edge.source == edge.target:
                continue
            seen.add(key)
            edge_rows.append({
                "source": edge.source,
                "target": edge.target,
                "line": edge.line,
            })

        with self._driver.session(database=self._database) as session:
            for batch in _chunks(node_rows):
                session.run(NODE_STATEMENT,
                            project_id=project_id, rows=batch)
            for batch in _chunks(edge_rows):
                session.run(EDGE_STATEMENT,
                            project_id=project_id, rows=batch)

    def blast_radius(self, project_id: str, path: str) -> list[str]:
        with self._driver.session(database=self._database) as session:
            result = session.run(BLAST_RADIUS_QUERY,
                                 project_id=project_id, path=path)
            return sorted(record["path"] for record in result)

    def close(self) -> None:
        self._driver.close()
