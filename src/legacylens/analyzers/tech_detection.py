"""Technology detection analyzer.

Builds the TechnologyProfile artifact from three deterministic sources:

1. Dependency names -> known frameworks (mapping table below, which also
   carries the endoflife.date product key each maps to)
2. File presence -> build tooling (pom.xml -> maven, Dockerfile -> docker)
3. Dockerfile FROM lines -> runtime images and their pinned versions
   (FROM python:2.7 is some of the strongest legacy evidence there is)

The endoflife.date product mapping is intentionally risk-tolerant: a wrong
product key just yields a 404 -> None from the client and the EOL analyzer
skips it. Growing this table is free of false-finding risk.
"""

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from legacylens.analyzers.base import Analyzer, AnalyzerResult
from legacylens.analyzers.registry import registry
from legacylens.domain.models import (
    Evidence,
    EvidenceSource,
    Finding,
    FindingCategory,
    ProjectContext,
    Severity,
)
from legacylens.parsing.manifests.models import DependencyInventory


class TechKind(StrEnum):
    LANGUAGE = "language"
    FRAMEWORK = "framework"
    RUNTIME = "runtime"
    BUILD_TOOL = "build_tool"
    DATABASE = "database"
    SERVICE = "service"


class DetectedTechnology(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    kind: TechKind
    version: str | None = None
    eol_product: str | None = None       # endoflife.date product key
    evidence: tuple[Evidence, ...] = ()


class TechnologyProfile(BaseModel):
    technologies: list[DetectedTechnology] = Field(default_factory=list)

    def with_eol_product(self) -> list[DetectedTechnology]:
        return [t for t in self.technologies if t.eol_product]


# dependency name (or maven groupId prefix) -> (display, kind, eol product)
_DEP_TECH_TABLE: dict[str, tuple[str, TechKind, str | None]] = {
    # PyPI
    "django": ("Django", TechKind.FRAMEWORK, "django"),
    "flask": ("Flask", TechKind.FRAMEWORK, "flask"),
    "fastapi": ("FastAPI", TechKind.FRAMEWORK, "fastapi"),
    "celery": ("Celery", TechKind.FRAMEWORK, None),
    "sqlalchemy": ("SQLAlchemy", TechKind.FRAMEWORK, "sqlalchemy"),
    # npm
    "react": ("React", TechKind.FRAMEWORK, "react"),
    "@angular/core": ("Angular", TechKind.FRAMEWORK, "angular"),
    "vue": ("Vue.js", TechKind.FRAMEWORK, "vue"),
    "express": ("Express", TechKind.FRAMEWORK, "express"),
    "jquery": ("jQuery", TechKind.FRAMEWORK, "jquery"),
    "webpack": ("Webpack", TechKind.BUILD_TOOL, None),
    # Maven groupId prefixes (matched on group segment)
    "org.springframework.boot": ("Spring Boot", TechKind.FRAMEWORK,
                                 "spring-boot"),
    "org.springframework": ("Spring Framework", TechKind.FRAMEWORK,
                            "spring-framework"),
    "org.hibernate": ("Hibernate ORM", TechKind.FRAMEWORK, "hibernate-orm"),
    "org.apache.struts": ("Apache Struts", TechKind.FRAMEWORK, None),
}

# Docker base image name -> (display, kind, eol product)
_IMAGE_TABLE: dict[str, tuple[str, TechKind, str | None]] = {
    "python": ("Python runtime", TechKind.RUNTIME, "python"),
    "node": ("Node.js runtime", TechKind.RUNTIME, "nodejs"),
    "java": ("Java runtime", TechKind.RUNTIME, "oracle-jdk"),
    "openjdk": ("OpenJDK runtime", TechKind.RUNTIME, "openjdk-builds-from-oracle"),
    "eclipse-temurin": ("Temurin JDK", TechKind.RUNTIME, "eclipse-temurin"),
    "mysql": ("MySQL", TechKind.DATABASE, "mysql"),
    "postgres": ("PostgreSQL", TechKind.DATABASE, "postgresql"),
    "mongo": ("MongoDB", TechKind.DATABASE, "mongodb"),
    "redis": ("Redis", TechKind.SERVICE, "redis"),
    "nginx": ("nginx", TechKind.SERVICE, "nginx"),
    "ubuntu": ("Ubuntu base image", TechKind.RUNTIME, "ubuntu"),
    "debian": ("Debian base image", TechKind.RUNTIME, "debian"),
    "alpine": ("Alpine base image", TechKind.RUNTIME, "alpine"),
}

_BUILD_TOOL_FILES: dict[str, str] = {
    "pom.xml": "Maven",
    "build.gradle": "Gradle",
    "build.gradle.kts": "Gradle",
    "package.json": "npm",
    "requirements.txt": "pip",
    "pyproject.toml": "pip/pyproject",
    "dockerfile": "Docker",
    "makefile": "Make",
    "docker-compose.yml": "Docker Compose",
    "docker-compose.yaml": "Docker Compose",
}

_FROM_LINE = re.compile(
    r"^\s*FROM\s+(?:--platform=\S+\s+)?(?P<image>[\w./-]+)"
    r"(?::(?P<tag>[\w.-]+))?",
    re.IGNORECASE,
)


@registry.register
class TechDetectionAnalyzer(Analyzer):
    id = "tech_detection"
    name = "Technology detection analyzer"
    depends_on = ("manifest_deps",)

    def analyze(self, ctx: ProjectContext) -> AnalyzerResult:
        profile = TechnologyProfile()
        seen: set[tuple[str, str | None]] = set()

        def add(tech: DetectedTechnology) -> None:
            key = (tech.name, tech.version)
            if key not in seen:
                seen.add(key)
                profile.technologies.append(tech)

        self._from_dependencies(ctx, add)
        self._from_build_files(ctx, add)
        self._from_dockerfiles(ctx, add)
        self._from_languages(ctx, add)

        summary = Finding(
            analyzer_id=self.id,
            rule_id="TECH-INV-001",
            category=FindingCategory.TECHNOLOGY,
            severity=Severity.INFO,
            title=f"{len(profile.technologies)} technologies detected",
            description=(
                "Detected technology surface: "
                + ", ".join(
                    f"{t.name}{' ' + t.version if t.version else ''}"
                    for t in profile.technologies
                )
            ),
            evidence=[Evidence(
                detail="aggregated from manifests, build files, Dockerfiles",
            )],
            metadata={"technologies": [
                t.model_dump(exclude={"evidence"})
                for t in profile.technologies
            ]},
        )
        return AnalyzerResult(findings=[summary], artifact=profile)

    def _from_dependencies(self, ctx: ProjectContext, add) -> None:
        inventory: DependencyInventory | None = ctx.get_artifact(
            "manifest_deps")
        if inventory is None:
            return
        for dep in inventory.dependencies:
            entry = _DEP_TECH_TABLE.get(dep.name)
            if entry is None and ":" in dep.name:   # maven group prefix
                group = dep.name.split(":", 1)[0]
                entry = _DEP_TECH_TABLE.get(group)
            if entry is None:
                continue
            name, kind, product = entry
            add(DetectedTechnology(
                name=name, kind=kind, version=dep.version,
                eol_product=product,
                evidence=(Evidence(
                    source=EvidenceSource.MANIFEST,
                    file_path=dep.manifest_path,
                    line_start=dep.line,
                    snippet=dep.raw_spec,
                ),),
            ))

    def _from_build_files(self, ctx: ProjectContext, add) -> None:
        for record in ctx.files:
            base = record.path.rsplit("/", 1)[-1].lower()
            tool = _BUILD_TOOL_FILES.get(base)
            if tool is None and base.startswith("dockerfile."):
                tool = "Docker"
            if tool:
                add(DetectedTechnology(
                    name=tool, kind=TechKind.BUILD_TOOL,
                    evidence=(Evidence(file_path=record.path),),
                ))

    def _from_dockerfiles(self, ctx: ProjectContext, add) -> None:
        for record in ctx.files_by_language("dockerfile"):
            text = (ctx.root / record.path).read_text(
                encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), start=1):
                match = _FROM_LINE.match(line)
                if not match:
                    continue
                image = match.group("image").rsplit("/", 1)[-1]
                entry = _IMAGE_TABLE.get(image)
                if entry is None:
                    continue
                name, kind, product = entry
                tag = match.group("tag")
                version = None
                if tag and tag != "latest":
                    version = re.split(r"[-_]", tag)[0]  # 2.7-alpine -> 2.7
                add(DetectedTechnology(
                    name=name, kind=kind, version=version,
                    eol_product=product,
                    evidence=(Evidence(
                        file_path=record.path,
                        line_start=lineno,
                        snippet=line.strip(),
                    ),),
                ))

    def _from_languages(self, ctx: ProjectContext, add) -> None:
        counts: dict[str, int] = {}
        for record in ctx.files:
            if record.language and not record.is_binary:
                counts[record.language] = counts.get(record.language, 0) + 1
        for language, count in sorted(counts.items(), key=lambda x: -x[1]):
            if count >= 3 and language in (
                "python", "java", "javascript", "typescript",
                "csharp", "go", "ruby", "php",
            ):
                add(DetectedTechnology(
                    name=language, kind=TechKind.LANGUAGE,
                    evidence=(Evidence(
                        detail=f"{count} {language} source files",
                    ),),
                ))
