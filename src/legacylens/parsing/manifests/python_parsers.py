"""Python ecosystem parsers: requirements.txt and pyproject.toml.

Handles PEP 508 requirement lines (extras, specifiers, environment markers)
plus both PEP 621 and Poetry layouts in pyproject.toml. URL/editable/option
lines in requirements files are skipped deliberately — they reference code,
not registry packages, and belong to a later repository-analysis rule.
"""

import re
import tomllib

from legacylens.parsing.manifests.base import ManifestParser, find_line
from legacylens.parsing.manifests.models import (
    DeclaredDependency,
    DependencyScope,
    Ecosystem,
)

_REQ_LINE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[[^\]]*\])?"          # extras
    r"\s*(?P<spec>[^;#]*)"       # version specifier
)
_EXACT_PIN = re.compile(r"==\s*[\w.!+]+$")
_POETRY_EXACT = re.compile(r"^\d[\w.!+]*$")


def _parse_requirement(raw: str, manifest_path: str, line: int,
                       scope: DependencyScope) -> DeclaredDependency | None:
    stripped = raw.strip()
    if (
        not stripped
        or stripped.startswith(("#", "-"))   # comments, -r/-e/--options
        or "://" in stripped                  # direct URL references
    ):
        return None
    match = _REQ_LINE.match(stripped)
    if not match:
        return None
    spec = match.group("spec").strip().rstrip(",")
    pinned = bool(_EXACT_PIN.fullmatch(spec)) and "*" not in spec
    version = spec.split("==", 1)[1].strip() if pinned else None
    return DeclaredDependency(
        name=match.group("name").lower(),
        ecosystem=Ecosystem.PYPI,
        raw_spec=stripped,
        version=version,
        scope=scope,
        manifest_path=manifest_path,
        line=line,
        is_pinned=pinned,
    )


class RequirementsTxtParser(ManifestParser):
    ecosystem = Ecosystem.PYPI

    def matches(self, rel_path: str) -> bool:
        name = self.basename(rel_path)
        return name.startswith("requirements") and name.endswith(".txt")

    def parse(self, text: str, manifest_path: str) -> list[DeclaredDependency]:
        scope = (
            DependencyScope.DEV
            if any(t in self.basename(manifest_path) for t in ("dev", "test"))
            else DependencyScope.RUNTIME
        )
        deps = []
        for lineno, raw in enumerate(text.splitlines(), start=1):
            dep = _parse_requirement(raw, manifest_path, lineno, scope)
            if dep:
                deps.append(dep)
        return deps


class PyprojectTomlParser(ManifestParser):
    ecosystem = Ecosystem.PYPI

    def matches(self, rel_path: str) -> bool:
        return self.basename(rel_path) == "pyproject.toml"

    def parse(self, text: str, manifest_path: str) -> list[DeclaredDependency]:
        data = tomllib.loads(text)
        deps: list[DeclaredDependency] = []

        project = data.get("project", {})
        for raw in project.get("dependencies", []):
            dep = _parse_requirement(
                raw, manifest_path,
                find_line(text, raw) or 0, DependencyScope.RUNTIME,
            )
            if dep:
                deps.append(dep)
        for group in project.get("optional-dependencies", {}).values():
            for raw in group:
                dep = _parse_requirement(
                    raw, manifest_path,
                    find_line(text, raw) or 0, DependencyScope.OPTIONAL,
                )
                if dep:
                    deps.append(dep)

        poetry = data.get("tool", {}).get("poetry", {})
        deps.extend(self._poetry_table(
            poetry.get("dependencies", {}), text, manifest_path,
            DependencyScope.RUNTIME,
        ))
        for group_name, group in poetry.get("group", {}).items():
            scope = (
                DependencyScope.DEV
                if group_name in ("dev", "test")
                else DependencyScope.OPTIONAL
            )
            deps.extend(self._poetry_table(
                group.get("dependencies", {}), text, manifest_path, scope,
            ))
        return deps

    def _poetry_table(self, table: dict, text: str, manifest_path: str,
                      scope: DependencyScope) -> list[DeclaredDependency]:
        deps = []
        for name, spec in table.items():
            if name.lower() == "python":
                continue
            raw = spec if isinstance(spec, str) else str(spec.get("version", ""))
            pinned = bool(_POETRY_EXACT.fullmatch(raw))
            deps.append(DeclaredDependency(
                name=name.lower(),
                ecosystem=Ecosystem.PYPI,
                raw_spec=f"{name} = {raw!r}",
                version=raw if pinned else None,
                scope=scope,
                manifest_path=manifest_path,
                line=find_line(text, name),
                is_pinned=pinned,
            ))
        return deps
