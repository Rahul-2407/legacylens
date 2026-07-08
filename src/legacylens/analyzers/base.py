"""The Analyzer contract.

Every analysis capability in the platform — technology detection, dependency
health, cycle detection, technical debt — is one class implementing this
interface. Adding a new capability means adding one file; nothing else in
the system changes. This is the extensibility guarantee of the platform.
"""

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from legacylens.domain.models import Finding, ProjectContext


class AnalyzerResult(BaseModel):
    """What one analyzer returns from a run.

    `findings` go to the evidence store. `artifact` is optional structured
    data made available to downstream analyzers via the ProjectContext
    blackboard (e.g. the manifest parser exposes a parsed dependency list
    that the EOL analyzer consumes).
    """

    findings: list[Finding] = Field(default_factory=list)
    artifact: Any = None


class Analyzer(ABC):
    """Base class for all analyzers.

    Class attributes:
        id:          unique stable identifier, e.g. "tech_detection"
        name:        human-readable name for reports and logs
        depends_on:  ids of analyzers that must run first; the registry
                     topologically sorts execution order from these
    """

    id: ClassVar[str]
    name: ClassVar[str]
    depends_on: ClassVar[tuple[str, ...]] = ()

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        for attr in ("id", "name"):
            if not getattr(cls, attr, None):
                raise TypeError(
                    f"Analyzer subclass {cls.__name__} must define '{attr}'"
                )

    @abstractmethod
    def analyze(self, ctx: ProjectContext) -> AnalyzerResult:
        """Analyze the project and return evidence-backed findings."""
        raise NotImplementedError
