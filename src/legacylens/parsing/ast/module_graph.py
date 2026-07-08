"""ModuleGraph — the artifact this layer publishes.

Nodes are project files; edges are import statements. Internal edges point
at resolved project files; external edges keep the raw specifier (package
name). Module 7 loads this structure into Neo4j for cycle detection and
migration ordering; the model here stays storage-agnostic on purpose.
"""

from collections import Counter

from pydantic import BaseModel, ConfigDict, Field


class ImportEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str                 # importing file (project-relative path)
    target: str                 # resolved file path, or raw external spec
    raw: str                    # specifier exactly as written
    line: int | None = None
    internal: bool
    broken: bool = False        # relative/internal-intent import, no target


class ModuleGraph(BaseModel):
    files: list[str] = Field(default_factory=list)   # parsed source files
    edges: list[ImportEdge] = Field(default_factory=list)

    @property
    def internal_edges(self) -> list[ImportEdge]:
        return [e for e in self.edges if e.internal]

    @property
    def broken_edges(self) -> list[ImportEdge]:
        return [e for e in self.edges if e.broken]

    def external_usage(self) -> Counter:
        """External specifier -> number of importing statements."""
        return Counter(e.target for e in self.edges
                       if not e.internal and not e.broken)

    def imports_of(self, file: str) -> list[str]:
        return [e.target for e in self.internal_edges if e.source == file]

    def importers_of(self, file: str) -> list[str]:
        return [e.source for e in self.internal_edges if e.target == file]
