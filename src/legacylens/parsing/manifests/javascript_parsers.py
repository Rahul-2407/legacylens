"""JavaScript ecosystem parser: package.json.

All four dependency sections are captured with their scopes. Exact-pin
detection follows npm semantics: bare semver is a pin; ^ ~ ranges,
wildcards, 'latest', and x-ranges are not.
"""

import json

from legacylens.parsing.manifests.base import ManifestParser, find_line
from legacylens.parsing.manifests.models import (
    DeclaredDependency,
    DependencyScope,
    Ecosystem,
)
import re

_EXACT_SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[\w.]+)?(\+[\w.]+)?$")

_SECTIONS: tuple[tuple[str, DependencyScope], ...] = (
    ("dependencies", DependencyScope.RUNTIME),
    ("devDependencies", DependencyScope.DEV),
    ("peerDependencies", DependencyScope.PEER),
    ("optionalDependencies", DependencyScope.OPTIONAL),
)


class PackageJsonParser(ManifestParser):
    ecosystem = Ecosystem.NPM

    def matches(self, rel_path: str) -> bool:
        return self.basename(rel_path) == "package.json"

    def parse(self, text: str, manifest_path: str) -> list[DeclaredDependency]:
        data = json.loads(text)
        deps: list[DeclaredDependency] = []
        for section, scope in _SECTIONS:
            for name, spec in data.get(section, {}).items():
                spec = str(spec).strip()
                pinned = bool(_EXACT_SEMVER.fullmatch(spec))
                deps.append(DeclaredDependency(
                    name=name,
                    ecosystem=Ecosystem.NPM,
                    raw_spec=f'"{name}": "{spec}"',
                    version=spec if pinned else None,
                    scope=scope,
                    manifest_path=manifest_path,
                    line=find_line(text, f'"{name}"'),
                    is_pinned=pinned,
                ))
        return deps
