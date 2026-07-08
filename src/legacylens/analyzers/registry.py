"""Analyzer registry.

Analyzers self-register with the @registry.register decorator. The registry
computes a valid execution order from each analyzer's depends_on declaration
using stdlib graphlib, rejecting unknown dependencies and cycles at
resolution time — misconfiguration fails fast, before any analysis runs.
"""

from graphlib import CycleError, TopologicalSorter

from legacylens.analyzers.base import Analyzer
from legacylens.core.exceptions import (
    CyclicDependencyError,
    DuplicateAnalyzerError,
    UnknownAnalyzerError,
)


class AnalyzerRegistry:
    def __init__(self) -> None:
        self._analyzers: dict[str, type[Analyzer]] = {}

    def register(self, analyzer_cls: type[Analyzer]) -> type[Analyzer]:
        """Class decorator: @registry.register above an Analyzer subclass."""
        analyzer_id = analyzer_cls.id
        if analyzer_id in self._analyzers:
            raise DuplicateAnalyzerError(analyzer_id)
        self._analyzers[analyzer_id] = analyzer_cls
        return analyzer_cls

    def get(self, analyzer_id: str) -> type[Analyzer]:
        try:
            return self._analyzers[analyzer_id]
        except KeyError:
            raise UnknownAnalyzerError(analyzer_id) from None

    @property
    def ids(self) -> list[str]:
        return sorted(self._analyzers)

    def resolve_order(
        self, selected: list[str] | None = None
    ) -> list[type[Analyzer]]:
        """Return analyzer classes in a dependency-valid execution order.

        If `selected` is given, the closure of its dependencies is included
        automatically — asking for the EOL analyzer transparently pulls in
        the manifest parser it depends on.
        """
        wanted = self._closure(selected) if selected else set(self._analyzers)

        sorter: TopologicalSorter[str] = TopologicalSorter()
        for analyzer_id in wanted:
            cls = self.get(analyzer_id)
            for dep in cls.depends_on:
                if dep not in self._analyzers:
                    raise UnknownAnalyzerError(dep, required_by=analyzer_id)
            sorter.add(analyzer_id, *cls.depends_on)

        try:
            ordered_ids = list(sorter.static_order())
        except CycleError as exc:
            raise CyclicDependencyError(
                f"Analyzer dependencies form a cycle: {exc.args[1]}"
            ) from exc

        return [self._analyzers[aid] for aid in ordered_ids]

    def _closure(self, selected: list[str]) -> set[str]:
        """Selected ids plus every transitive dependency."""
        result: set[str] = set()
        stack = list(selected)
        while stack:
            analyzer_id = stack.pop()
            if analyzer_id in result:
                continue
            cls = self.get(analyzer_id)  # raises UnknownAnalyzerError
            result.add(analyzer_id)
            stack.extend(cls.depends_on)
        return result

    def clear(self) -> None:
        """Remove all registrations (test isolation helper)."""
        self._analyzers.clear()


# Process-wide default registry. Analyzer modules import this and decorate
# their classes; the pipeline resolves execution order from it.
registry = AnalyzerRegistry()
