"""Registry tests: ordering, dependency closure, and failure modes."""

import pytest

from legacylens.analyzers.base import Analyzer, AnalyzerResult
from legacylens.analyzers.registry import AnalyzerRegistry
from legacylens.core.exceptions import (
    CyclicDependencyError,
    DuplicateAnalyzerError,
    UnknownAnalyzerError,
)
from legacylens.domain.models import ProjectContext


def make_analyzer(analyzer_id: str, deps: tuple[str, ...] = ()) -> type[Analyzer]:
    return type(
        f"Analyzer_{analyzer_id}",
        (Analyzer,),
        {
            "id": analyzer_id,
            "name": analyzer_id,
            "depends_on": deps,
            "analyze": lambda self, ctx: AnalyzerResult(),
        },
    )


@pytest.fixture()
def reg() -> AnalyzerRegistry:
    return AnalyzerRegistry()


class TestRegistration:
    def test_duplicate_id_rejected(self, reg):
        reg.register(make_analyzer("tech"))
        with pytest.raises(DuplicateAnalyzerError):
            reg.register(make_analyzer("tech"))

    def test_subclass_without_id_rejected_at_definition(self):
        with pytest.raises(TypeError):
            type(
                "Nameless",
                (Analyzer,),
                {"analyze": lambda self, ctx: AnalyzerResult()},
            )


class TestOrdering:
    def test_dependencies_run_before_dependents(self, reg):
        reg.register(make_analyzer("eol", deps=("manifest",)))
        reg.register(make_analyzer("manifest", deps=("ingest",)))
        reg.register(make_analyzer("ingest"))
        order = [cls.id for cls in reg.resolve_order()]
        assert order.index("ingest") < order.index("manifest") < order.index("eol")

    def test_selection_pulls_in_transitive_dependencies(self, reg):
        reg.register(make_analyzer("eol", deps=("manifest",)))
        reg.register(make_analyzer("manifest", deps=("ingest",)))
        reg.register(make_analyzer("ingest"))
        reg.register(make_analyzer("unrelated"))
        order = [cls.id for cls in reg.resolve_order(["eol"])]
        assert order == ["ingest", "manifest", "eol"]
        assert "unrelated" not in order

    def test_cycle_rejected(self, reg):
        reg.register(make_analyzer("a", deps=("b",)))
        reg.register(make_analyzer("b", deps=("a",)))
        with pytest.raises(CyclicDependencyError):
            reg.resolve_order()

    def test_unknown_dependency_rejected_with_context(self, reg):
        reg.register(make_analyzer("a", deps=("ghost",)))
        with pytest.raises(UnknownAnalyzerError) as excinfo:
            reg.resolve_order()
        assert "ghost" in str(excinfo.value)
        assert "a" in str(excinfo.value)

    def test_unknown_selection_rejected(self, reg):
        reg.register(make_analyzer("a"))
        with pytest.raises(UnknownAnalyzerError):
            reg.resolve_order(["nope"])
