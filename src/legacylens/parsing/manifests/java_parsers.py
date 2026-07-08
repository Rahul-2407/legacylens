"""Java ecosystem parsers: Maven pom.xml and Gradle build files.

pom.xml gets real parsing: XML with namespace stripping, <properties>
resolution (including project.version), and dependencyManagement — this is
the manifest format legacy enterprise Java lives in, so it gets the most
care. Version ranges, LATEST/RELEASE, and unresolvable properties are all
recorded honestly (is_pinned=False or None) rather than guessed.

build.gradle is Groovy/Kotlin code, not data — full fidelity needs a real
interpreter. The regex extraction here captures the dominant
'group:artifact:version' notation and is explicitly best-effort.
"""

import re
import xml.etree.ElementTree as ET

from legacylens.parsing.manifests.base import ManifestParser, find_line
from legacylens.parsing.manifests.models import (
    DeclaredDependency,
    DependencyScope,
    Ecosystem,
)

_PROPERTY_REF = re.compile(r"^\$\{(?P<key>[^}]+)\}$")
_SCOPE_MAP = {
    "test": DependencyScope.TEST,
    "provided": DependencyScope.RUNTIME,
    "runtime": DependencyScope.RUNTIME,
    "compile": DependencyScope.RUNTIME,
    "optional": DependencyScope.OPTIONAL,
}


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(elem: ET.Element, name: str) -> str | None:
    for child in elem:
        if _strip_ns(child.tag) == name and child.text:
            return child.text.strip()
    return None


class PomXmlParser(ManifestParser):
    ecosystem = Ecosystem.MAVEN

    def matches(self, rel_path: str) -> bool:
        return self.basename(rel_path) == "pom.xml"

    def parse(self, text: str, manifest_path: str) -> list[DeclaredDependency]:
        root = ET.fromstring(text)
        properties = self._collect_properties(root)
        deps: list[DeclaredDependency] = []

        for dep_elem in root.iter():
            if _strip_ns(dep_elem.tag) != "dependency":
                continue
            group = _child_text(dep_elem, "groupId")
            artifact = _child_text(dep_elem, "artifactId")
            if not group or not artifact:
                continue
            raw_version = _child_text(dep_elem, "version")
            version, pinned = self._resolve_version(raw_version, properties)
            scope_text = _child_text(dep_elem, "scope") or "compile"
            deps.append(DeclaredDependency(
                name=f"{group}:{artifact}",
                ecosystem=Ecosystem.MAVEN,
                raw_spec=f"{group}:{artifact}:{raw_version or '(managed)'}",
                version=version,
                scope=_SCOPE_MAP.get(scope_text, DependencyScope.RUNTIME),
                manifest_path=manifest_path,
                line=find_line(text, f"<artifactId>{artifact}</artifactId>"),
                is_pinned=pinned,
            ))
        return deps

    def _collect_properties(self, root: ET.Element) -> dict[str, str]:
        props: dict[str, str] = {}
        for elem in root.iter():
            if _strip_ns(elem.tag) == "properties":
                for child in elem:
                    if child.text:
                        props[_strip_ns(child.tag)] = child.text.strip()
        for name in ("version", "groupId"):
            direct = None
            for child in root:
                if _strip_ns(child.tag) == name and child.text:
                    direct = child.text.strip()
            if direct:
                props[f"project.{name}"] = direct
        return props

    def _resolve_version(
        self, raw: str | None, properties: dict[str, str]
    ) -> tuple[str | None, bool | None]:
        if raw is None:
            return None, None            # managed by parent/BOM — unknown here
        ref = _PROPERTY_REF.match(raw)
        if ref:
            resolved = properties.get(ref.group("key"))
            if resolved is None:
                return None, None        # property defined elsewhere — honest None
            raw = resolved
        if raw.upper() in ("LATEST", "RELEASE") or any(c in raw for c in "[]()"):
            return raw, False            # dynamic or range — not a pin
        return raw, True


_GRADLE_DEP = re.compile(
    r"(?P<conf>implementation|api|compileOnly|runtimeOnly|testImplementation|"
    r"testRuntimeOnly|annotationProcessor|compile|testCompile|runtime)"
    r"\s*\(?\s*['\"](?P<group>[\w.\-]+):(?P<artifact>[\w.\-]+):(?P<version>[^'\"]+)['\"]"
)


class GradleParser(ManifestParser):
    ecosystem = Ecosystem.MAVEN

    def matches(self, rel_path: str) -> bool:
        return self.basename(rel_path) in ("build.gradle", "build.gradle.kts")

    def parse(self, text: str, manifest_path: str) -> list[DeclaredDependency]:
        deps = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = _GRADLE_DEP.search(line)
            if not match:
                continue
            version = match.group("version")
            dynamic = "$" in version or "+" in version
            scope = (
                DependencyScope.TEST
                if match.group("conf").startswith("test")
                else DependencyScope.RUNTIME
            )
            deps.append(DeclaredDependency(
                name=f"{match.group('group')}:{match.group('artifact')}",
                ecosystem=Ecosystem.MAVEN,
                raw_spec=match.group(0),
                version=None if dynamic else version,
                scope=scope,
                manifest_path=manifest_path,
                line=lineno,
                is_pinned=None if "$" in version else not dynamic,
            ))
        return deps
