"""Service-layer tests: repository roundtrip, the full run_analysis
lifecycle against tmp SQLite (success and failure), and the real HTTP
surface via TestClient with a synchronous enqueue — no Redis anywhere."""

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from legacylens.api.app import create_app
from legacylens.core.config import Settings, get_settings
from legacylens.db import session as repo
from legacylens.db.session import make_session_factory
from legacylens.domain.models import (
    Evidence,
    Finding,
    FindingCategory,
    Severity,
)
from legacylens.service.analysis import run_analysis


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("LEGACYLENS_OFFLINE_MODE", "true")
    monkeypatch.setenv("LEGACYLENS_WORKSPACE_DIR", str(tmp_path / "ws"))
    monkeypatch.setenv("LEGACYLENS_DATABASE_URL",
                       f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("LEGACYLENS_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("LEGACYLENS_GROQ_API_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def settings() -> Settings:
    return get_settings()


@pytest.fixture()
def session_factory(settings):
    return make_session_factory(settings)


def make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("app/__init__.py", "")
        zf.writestr("app/orders.py", "from app import billing\n")
        zf.writestr("app/billing.py", "from app import orders\n")
        zf.writestr("app/main.py", "from app import orders\n")
        zf.writestr("requirements.txt", "django==1.11.29\nrequests>=2.0\n")
    return buf.getvalue()


class TestRepository:
    def test_full_roundtrip(self, session_factory):
        finding = Finding(
            analyzer_id="x", rule_id="R-1",
            category=FindingCategory.SECURITY, severity=Severity.HIGH,
            title="t", description="d",
            evidence=[Evidence(file_path="a.py", line_start=3)],
            metadata={"k": 1},
        )
        with session_factory() as s:
            repo.create_project(s, "p1", "demo.zip")
            repo.save_findings(s, "p1", [finding])
            repo.save_artifact(s, "p1", "report_md", "# hello")
            repo.save_artifact(s, "p1", "report_md", "# updated")  # upsert
            repo.set_status(s, "p1", "completed", file_count=5,
                            readiness=80)

        with session_factory() as s:
            record = repo.get_project(s, "p1")
            assert (record.status, record.readiness) == ("completed", 80)
            rows = repo.get_findings(s, "p1", severity="high")
            assert rows[0].finding_id == finding.finding_id
            assert rows[0].evidence[0]["file_path"] == "a.py"
            assert repo.get_findings(s, "p1", severity="low") == []
            assert repo.get_artifact(s, "p1", "report_md") == "# updated"
            assert repo.get_artifact(s, "p1", "missing") is None


class TestRunAnalysis:
    def test_success_lifecycle(self, tmp_path, session_factory, settings):
        archive = tmp_path / "upload.zip"
        archive.write_bytes(make_zip())
        with session_factory() as s:
            repo.create_project(s, "proj-ok", "upload.zip")

        run_analysis("proj-ok", str(archive), session_factory, settings)

        with session_factory() as s:
            record = repo.get_project(s, "proj-ok")
            assert record.status == "completed"
            assert record.file_count == 5
            assert 0 <= record.readiness <= 100
            findings = repo.get_findings(s, "proj-ok")
            assert any(f.rule_id == "ARCH-CYCLE-001" for f in findings)
            report = repo.get_artifact(s, "proj-ok", "report_md")
            assert "# LegacyLens Migration Assessment" in report
            scorecard = json.loads(
                repo.get_artifact(s, "proj-ok", "scorecard"))
            assert scorecard["readiness"]["value"] == record.readiness
            mermaid = repo.get_artifact(s, "proj-ok", "mermaid_modules")
            assert mermaid and mermaid.startswith("flowchart TD")

    def test_failure_is_recorded_never_stuck_running(
            self, tmp_path, session_factory, settings):
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"this is not a zip")
        with session_factory() as s:
            repo.create_project(s, "proj-bad", "bad.zip")

        run_analysis("proj-bad", str(bad), session_factory, settings)

        with session_factory() as s:
            record = repo.get_project(s, "proj-bad")
            assert record.status == "failed"
            assert "Not a valid zip" in record.error


class TestApi:
    @pytest.fixture()
    def client(self, settings, session_factory):
        def sync_enqueue(project_id: str, archive_path: str) -> None:
            run_analysis(project_id, archive_path, session_factory,
                         settings)

        app = create_app(settings=settings,
                         session_factory=session_factory,
                         enqueue=sync_enqueue)
        return TestClient(app)

    def test_upload_to_report_journey(self, client):
        response = client.post(
            "/projects",
            files={"file": ("legacy.zip", make_zip(), "application/zip")},
        )
        assert response.status_code == 202
        project_id = response.json()["project_id"]

        status = client.get(f"/projects/{project_id}").json()
        assert status["status"] == "completed"       # sync enqueue
        assert status["readiness"] is not None

        high = client.get(f"/projects/{project_id}/findings",
                          params={"severity": "high"}).json()
        assert any(f["rule_id"] == "ARCH-CYCLE-001" for f in high)
        assert all(f["severity"] == "high" for f in high)

        report = client.get(f"/projects/{project_id}/report")
        assert report.status_code == 200
        assert "text/markdown" in report.headers["content-type"]
        assert "## Findings appendix" in report.text

        scorecard = client.get(f"/projects/{project_id}/scorecard").json()
        assert scorecard["readiness"]["value"] == status["readiness"]

        listing = client.get("/projects").json()
        assert listing[0]["project_id"] == project_id

    def test_non_zip_rejected(self, client):
        response = client.post(
            "/projects",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 400

    def test_unknown_project_404(self, client):
        assert client.get("/projects/proj-ghost").status_code == 404
        assert client.get("/projects/proj-ghost/report").status_code == 404

    def test_report_conflicts_until_completed(self, client, session_factory):
        with session_factory() as s:
            repo.create_project(s, "proj-pending", "x.zip")
        response = client.get("/projects/proj-pending/report")
        assert response.status_code == 409
        assert "pending" in response.json()["detail"]


class TestCeleryWiring:
    def test_task_registered_with_stable_name(self):
        from legacylens.service.tasks import analyze_project, celery_app
        assert "legacylens.analyze_project" in celery_app.tasks
        assert analyze_project.max_retries == 0
        assert celery_app.conf.task_acks_late is True
