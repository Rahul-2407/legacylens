"""Dependency models shared across all ecosystems.

Downstream analyzers (EOL, vulnerabilities, upgrade-distance) consume this
normalized form and never touch raw manifests — parser complexity stays in
one place.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Ecosystem(StrEnum):
    PYPI = "pypi"
    NPM = "npm"
    MAVEN = "maven"


class DependencyScope(StrEnum):
    RUNTIME = "runtime"
    DEV = "dev"
    TEST = "test"
    PEER = "peer"
    OPTIONAL = "optional"


class DeclaredDependency(BaseModel):
    """One dependency declaration found in one manifest."""

    model_config = ConfigDict(frozen=True)

    name: str
    ecosystem: Ecosystem
    raw_spec: str                    # exactly as written in the manifest
    version: str | None = None       # best-effort extracted version
    scope: DependencyScope = DependencyScope.RUNTIME
    manifest_path: str
    line: int | None = None
    # True = exact pin, False = range/wildcard, None = undeterminable
    # (e.g. unresolved Maven property)
    is_pinned: bool | None = None


class DependencyInventory(BaseModel):
    """The artifact the manifest analyzer publishes to the blackboard."""

    dependencies: list[DeclaredDependency] = Field(default_factory=list)
    manifest_paths: list[str] = Field(default_factory=list)

    def by_ecosystem(self, ecosystem: Ecosystem) -> list[DeclaredDependency]:
        return [d for d in self.dependencies if d.ecosystem == ecosystem]

    @property
    def runtime_dependencies(self) -> list[DeclaredDependency]:
        return [
            d for d in self.dependencies if d.scope == DependencyScope.RUNTIME
        ]
