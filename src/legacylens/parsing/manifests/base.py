"""Manifest parser contract.

A parser answers two questions: "is this file mine?" (by path) and "what
dependencies does it declare?" (from text). Parsers are pure functions of
file content — no filesystem access — which keeps them trivially testable.
"""

from abc import ABC, abstractmethod
from pathlib import PurePosixPath
from typing import ClassVar

from legacylens.parsing.manifests.models import DeclaredDependency, Ecosystem


class ManifestParser(ABC):
    ecosystem: ClassVar[Ecosystem]

    @abstractmethod
    def matches(self, rel_path: str) -> bool:
        """True if this parser handles the file at rel_path."""

    @abstractmethod
    def parse(self, text: str, manifest_path: str) -> list[DeclaredDependency]:
        """Extract dependency declarations. Raises on malformed input."""

    @staticmethod
    def basename(rel_path: str) -> str:
        return PurePosixPath(rel_path).name.lower()


def find_line(text: str, needle: str) -> int | None:
    """Best-effort 1-based line number of the first occurrence of needle.

    Used for formats (JSON, TOML tables) whose parsers discard positions.
    """
    for lineno, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return lineno
    return None
