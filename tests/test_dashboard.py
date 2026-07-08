"""Dashboard tests: the API client against MockTransport, pure helpers,
and Streamlit AppTest smoke tests driving both pages with a fake client."""

import json

import httpx
import pytest
from streamlit.testing.v1 import AppTest

import dashboard.api_client as api_client_module
from dashboard.api_client import ApiClient, DashboardApiError
from dashboard.helpers import findings_df, mermaid_html, risk_matrix_df

APP_PATH = "dashboard/app.py"


def make_client(handler) -> ApiClient:
    return ApiClient(base_url="http://api.test",
                     transport=httpx.MockTransport(handler))


class TestApiClient:
    def test_upload_multipart_and_202(self):
        captured = {}

        def handler(request):
            captured["path"] = request.url.path
            captured["content_type"] = request.headers["content-type"]
            return httpx.Response(202, json={"project_id": "proj-1",
                                             "status": "pending"})

        client = make_client(handler)
        created = client.upload("legacy.zip", b"PK...")
        assert created["project_id"] == "proj-1"
        assert captured["path"] == "/projects"
        assert "multipart/form-data" in captured["content_type"]

    def test_error_statuses_raise_with_context(self):
        client = make_client(lambda r: httpx.Response(500, text="boom"))
        with pytest.raises(DashboardApiError, match="HTTP 500"):
            client.list_projects()

    def test_artifact_404_is_none_but_other_errors_raise(self):
        client = make_client(lambda r: httpx.Response(404, text="nope"))
        assert client.artifact("p1", "mermaid_waves") is None
        client = make_client(lambda r: httpx.Response(503, text="down"))
        with pytest.raises(DashboardApiError):
            client.artifact("p1", "mermaid_waves")

    def test_health_never_raises(self):
        def handler(request):
            raise httpx.ConnectError("refused")
        assert make_client(handler).health() is False


class TestHelpers:
    def test_risk_matrix_df_ordering(self):
        scorecard = {"risk_matrix": {
            "medium": {"database": 2},
            "critical": {"technology": 1, "database": 1},
        }}
        df = risk_matrix_df(scorecard)
        assert list(df.index) == ["critical", "medium"]  # severity order
        assert df.loc["critical", "technology"] == 1
        assert df.loc["medium", "technology"] == 0       # filled zero

    def test_findings_df_sorted_by_severity(self):
        df = findings_df([
            {"severity": "low", "rule_id": "B", "title": "t",
             "finding_id": "F-2"},
            {"severity": "critical", "rule_id": "A", "title": "t",
             "finding_id": "F-1"},
        ])
        assert list(df["severity"]) == ["critical", "low"]

    def test_mermaid_html_embeds_diagram(self):
        html = mermaid_html("flowchart TD\n  a --> b")
        assert "flowchart TD" in html and "mermaid" in html


PROJECTS = [
    {"project_id": "proj-demo", "name": "shop.zip", "status": "completed",
     "file_count": 6, "readiness": 72, "error": None},
    {"project_id": "proj-run", "name": "erp.zip", "status": "running",
     "file_count": None, "readiness": None, "error": None},
]
SCORECARD = {
    "readiness": {"value": 72, "components": [
        {"name": "high_findings", "detail": "2 high finding(s) × 8",
         "delta": -16}]},
    "risk_matrix": {"high": {"architecture": 1, "security": 1}},
    "effort": {"expected_days": 12.5, "optimistic_days": 8.8,
               "pessimistic_days": 16.3, "band_counts": {"low": 4},
               "multipliers_applied": [], "assumptions": ["heuristic"]},
    "top_risks": [], "quick_wins": [], "high_risk_modules": [],
    "module_scores": {},
}
FINDINGS = [{
    "finding_id": "F-abc123def456", "rule_id": "ARCH-CYCLE-001",
    "analyzer_id": "architecture", "category": "architecture",
    "severity": "high", "title": "Cyclic dependency between 2 modules",
    "description": "one indivisible migration unit",
    "evidence": [{"file_path": "app/orders.py", "line_start": 1,
                  "snippet": "from app import billing"}],
    "metadata": {},
}]


class FakeClient:
    base_url = "http://fake.test"

    def __init__(self, *args, **kwargs):
        pass

    def health(self):
        return True

    def list_projects(self):
        return PROJECTS

    def get_project(self, project_id):
        return PROJECTS[0]

    def upload(self, filename, data):
        return {"project_id": "proj-new", "status": "pending"}

    def findings(self, project_id, severity=None):
        return [f for f in FINDINGS
                if severity is None or f["severity"] == severity]

    def scorecard(self, project_id):
        return SCORECARD

    def report(self, project_id):
        return "# LegacyLens Migration Assessment — proj-demo\nbody"

    def artifact(self, project_id, kind):
        return "flowchart TD\n  a --> b" if kind == "mermaid_modules" \
            else None


@pytest.fixture()
def fake_api(monkeypatch):
    monkeypatch.setattr(api_client_module, "ApiClient", FakeClient)


class TestAppSmoke:
    def test_projects_page_renders(self, fake_api):
        at = AppTest.from_file(APP_PATH, default_timeout=15).run()
        assert not at.exception
        assert "LegacyLens" in at.title[0].value
        # the projects table made it to the page
        frame = at.dataframe[0].value
        assert "proj-demo" in frame["Project"].tolist()

    def test_detail_page_renders_scorecard_and_findings(self, fake_api):
        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()
        at.sidebar.radio[0].set_value("Project detail").run()
        assert not at.exception
        assert at.metric[0].value == "72/100"
        expander_labels = [e.label for e in at.expander]
        assert any("ARCH-CYCLE-001" in label for label in expander_labels)
        assert any("Why this readiness score?" in label
                   for label in expander_labels)
