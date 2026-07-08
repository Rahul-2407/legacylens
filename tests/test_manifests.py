"""Manifest parsing tests: realistic fixtures per ecosystem, then the
analyzer end-to-end with its three deterministic rules."""

import textwrap
from pathlib import Path

import pytest

from legacylens.analyzers.manifest_deps import ManifestDependencyAnalyzer
from legacylens.domain.models import FileRecord, ProjectContext
from legacylens.parsing.manifests.java_parsers import GradleParser, PomXmlParser
from legacylens.parsing.manifests.javascript_parsers import PackageJsonParser
from legacylens.parsing.manifests.models import DependencyScope, Ecosystem
from legacylens.parsing.manifests.python_parsers import (
    PyprojectTomlParser,
    RequirementsTxtParser,
)


class TestRequirementsTxt:
    TEXT = textwrap.dedent("""\
        # pinned production deps
        flask==0.12.4
        requests[security]>=2.0,<3.0
        gunicorn == 19.9.0
        -r base.txt
        git+https://github.com/org/pkg.git#egg=pkg
        celery
    """)

    def test_parse(self):
        deps = RequirementsTxtParser().parse(self.TEXT, "requirements.txt")
        by_name = {d.name: d for d in deps}

        assert set(by_name) == {"flask", "requests", "gunicorn", "celery"}
        assert by_name["flask"].is_pinned and by_name["flask"].version == "0.12.4"
        assert by_name["flask"].line == 2
        assert by_name["requests"].is_pinned is False
        assert by_name["gunicorn"].is_pinned  # whitespace around ==
        assert by_name["celery"].is_pinned is False  # bare name floats

    def test_dev_requirements_get_dev_scope(self):
        deps = RequirementsTxtParser().parse(
            "pytest==8.0.0\n", "requirements-dev.txt"
        )
        assert deps[0].scope == DependencyScope.DEV

    def test_matches(self):
        parser = RequirementsTxtParser()
        assert parser.matches("requirements.txt")
        assert parser.matches("deploy/requirements-prod.txt")
        assert not parser.matches("docs/notes.txt")


class TestPyprojectToml:
    TEXT = textwrap.dedent("""\
        [project]
        dependencies = ["fastapi==0.110.0", "uvicorn>=0.29"]

        [project.optional-dependencies]
        dev = ["pytest==8.0.0"]

        [tool.poetry.dependencies]
        python = "^3.12"
        langchain = "0.2.1"
        chromadb = "^0.5"

        [tool.poetry.group.dev.dependencies]
        ruff = "^0.4"
    """)

    def test_parse(self):
        deps = PyprojectTomlParser().parse(self.TEXT, "pyproject.toml")
        by_name = {d.name: d for d in deps}

        assert "python" not in by_name
        assert by_name["fastapi"].is_pinned
        assert by_name["uvicorn"].is_pinned is False
        assert by_name["pytest"].scope == DependencyScope.OPTIONAL
        assert by_name["langchain"].is_pinned and by_name["langchain"].version == "0.2.1"
        assert by_name["chromadb"].is_pinned is False  # caret range
        assert by_name["ruff"].scope == DependencyScope.DEV


class TestPackageJson:
    TEXT = textwrap.dedent("""\
        {
          "name": "legacy-ui",
          "dependencies": {
            "react": "16.8.0",
            "lodash": "^4.17.0",
            "left-pad": "*"
          },
          "devDependencies": { "webpack": "~4.0.0" },
          "peerDependencies": { "react-dom": ">=16" }
        }
    """)

    def test_parse(self):
        deps = PackageJsonParser().parse(self.TEXT, "package.json")
        by_name = {d.name: d for d in deps}

        assert by_name["react"].is_pinned and by_name["react"].version == "16.8.0"
        assert by_name["react"].line == 4
        assert by_name["lodash"].is_pinned is False
        assert by_name["left-pad"].is_pinned is False
        assert by_name["webpack"].scope == DependencyScope.DEV
        assert by_name["react-dom"].scope == DependencyScope.PEER


class TestPomXml:
    TEXT = textwrap.dedent("""\
        <project xmlns="http://maven.apache.org/POM/4.0.0">
          <version>2.4.1</version>
          <properties>
            <spring.version>4.3.9.RELEASE</spring.version>
          </properties>
          <dependencies>
            <dependency>
              <groupId>org.springframework</groupId>
              <artifactId>spring-core</artifactId>
              <version>${spring.version}</version>
            </dependency>
            <dependency>
              <groupId>com.corp</groupId>
              <artifactId>corp-commons</artifactId>
              <version>${project.version}</version>
            </dependency>
            <dependency>
              <groupId>junit</groupId>
              <artifactId>junit</artifactId>
              <version>4.12</version>
              <scope>test</scope>
            </dependency>
            <dependency>
              <groupId>com.corp</groupId>
              <artifactId>managed-lib</artifactId>
            </dependency>
            <dependency>
              <groupId>com.corp</groupId>
              <artifactId>mystery</artifactId>
              <version>${undefined.prop}</version>
            </dependency>
          </dependencies>
        </project>
    """)

    def test_property_resolution_and_scopes(self):
        deps = PomXmlParser().parse(self.TEXT, "pom.xml")
        by_name = {d.name: d for d in deps}

        spring = by_name["org.springframework:spring-core"]
        assert spring.version == "4.3.9.RELEASE" and spring.is_pinned

        commons = by_name["com.corp:corp-commons"]
        assert commons.version == "2.4.1"  # ${project.version} resolved

        junit = by_name["junit:junit"]
        assert junit.scope == DependencyScope.TEST

        assert by_name["com.corp:managed-lib"].is_pinned is None
        assert by_name["com.corp:mystery"].is_pinned is None  # honest unknown


class TestGradle:
    TEXT = textwrap.dedent("""\
        dependencies {
            implementation 'org.springframework:spring-web:4.3.9.RELEASE'
            implementation("com.google.guava:guava:31.1-jre")
            testImplementation 'junit:junit:4.+'
            implementation "com.corp:lib:${corpVersion}"
        }
    """)

    def test_parse(self):
        deps = GradleParser().parse(self.TEXT, "build.gradle")
        by_name = {d.name: d for d in deps}

        assert by_name["org.springframework:spring-web"].is_pinned
        assert by_name["com.google.guava:guava"].version == "31.1-jre"
        junit = by_name["junit:junit"]
        assert junit.scope == DependencyScope.TEST and junit.is_pinned is False
        assert by_name["com.corp:lib"].is_pinned is None  # interpolated


class TestManifestAnalyzer:
    def make_project(self, tmp_path: Path) -> ProjectContext:
        (tmp_path / "requirements.txt").write_text(
            "flask==0.12.4\nrequests>=2.0\nrequests>=2.1\n"
        )
        (tmp_path / "ui").mkdir()
        (tmp_path / "ui/package.json").write_text(
            '{"dependencies": {"react": "^16.0.0"}}'
        )
        (tmp_path / "broken").mkdir()
        (tmp_path / "broken/package.json").write_text("{not valid json")
        files = [
            FileRecord(path="requirements.txt", size_bytes=1),
            FileRecord(path="ui/package.json", size_bytes=1),
            FileRecord(path="broken/package.json", size_bytes=1),
            FileRecord(path="src/app.py", size_bytes=1, language="python"),
        ]
        return ProjectContext(project_id="p1", root=tmp_path, files=files)

    def test_end_to_end(self, tmp_path):
        ctx = self.make_project(tmp_path)
        result = ManifestDependencyAnalyzer().analyze(ctx)

        inventory = result.artifact
        assert sorted(inventory.manifest_paths) == [
            "requirements.txt", "ui/package.json",
        ]
        pypi = inventory.by_ecosystem(Ecosystem.PYPI)
        assert {d.name for d in pypi} == {"flask", "requests"}
        assert len(pypi) == 3  # requests declared twice, both preserved

        rules = {f.rule_id for f in result.findings}
        assert rules == {"MAN-PARSE-001", "DEP-UNPINNED-001", "DEP-DUP-001"}

        unpinned = [
            f for f in result.findings if f.rule_id == "DEP-UNPINNED-001"
        ]
        # requirements.txt (requests floats) and package.json (^16 floats)
        assert len(unpinned) == 2
        for finding in unpinned:
            assert all(ev.file_path for ev in finding.evidence)
            assert all(ev.snippet for ev in finding.evidence)

        parse_failure = next(
            f for f in result.findings if f.rule_id == "MAN-PARSE-001"
        )
        assert "broken/package.json" in parse_failure.title

    def test_evidence_cap(self, tmp_path):
        lines = "\n".join(f"pkg{i}>=1.0" for i in range(50))
        (tmp_path / "requirements.txt").write_text(lines)
        ctx = ProjectContext(
            project_id="p1", root=tmp_path,
            files=[FileRecord(path="requirements.txt", size_bytes=1)],
        )
        result = ManifestDependencyAnalyzer().analyze(ctx)
        finding = next(
            f for f in result.findings if f.rule_id == "DEP-UNPINNED-001"
        )
        assert len(finding.evidence) == 20          # capped
        assert finding.metadata["count"] == 50      # true total preserved
