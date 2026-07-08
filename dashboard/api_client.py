"""Dashboard API client.

The dashboard's ONLY connection to the platform — pure HTTP against the
FastAPI service, no imports from the analysis engine. That boundary is
what makes the dashboard a swappable presentation layer.

Transport is injectable (httpx.MockTransport in tests); base URL comes
from LEGACYLENS_API_URL (default http://localhost:8000).
"""

import os

import httpx


class DashboardApiError(Exception):
    pass


class ApiClient:
    def __init__(self, base_url: str | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = (base_url
                         or os.environ.get("LEGACYLENS_API_URL")
                         or "http://localhost:8000")
        self._client = httpx.Client(base_url=self.base_url,
                                    transport=transport, timeout=30.0)

    def _get(self, path: str, **params) -> httpx.Response:
        try:
            response = self._client.get(path, params=params or None)
        except httpx.HTTPError as exc:
            raise DashboardApiError(f"API unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise DashboardApiError(
                f"GET {path} -> HTTP {response.status_code}: "
                f"{response.text[:200]}")
        return response

    def health(self) -> bool:
        try:
            return self._get("/health").json().get("status") == "ok"
        except DashboardApiError:
            return False

    def list_projects(self) -> list[dict]:
        return self._get("/projects").json()

    def get_project(self, project_id: str) -> dict:
        return self._get(f"/projects/{project_id}").json()

    def upload(self, filename: str, data: bytes) -> dict:
        try:
            response = self._client.post(
                "/projects",
                files={"file": (filename, data, "application/zip")})
        except httpx.HTTPError as exc:
            raise DashboardApiError(f"API unreachable: {exc}") from exc
        if response.status_code != 202:
            raise DashboardApiError(
                f"Upload rejected (HTTP {response.status_code}): "
                f"{response.text[:200]}")
        return response.json()

    def delete(self, project_id: str) -> None:
        try:
            response = self._client.delete(f"/projects/{project_id}")
        except httpx.HTTPError as exc:
            raise DashboardApiError(f"API unreachable: {exc}") from exc
        if response.status_code not in (204, 200):
            raise DashboardApiError(
                f"Delete failed (HTTP {response.status_code}): "
                f"{response.text[:200]}")

    def findings(self, project_id: str,
                 severity: str | None = None) -> list[dict]:
        params = {"severity": severity} if severity else {}
        return self._get(f"/projects/{project_id}/findings",
                         **params).json()

    def scorecard(self, project_id: str) -> dict:
        return self._get(f"/projects/{project_id}/scorecard").json()

    def report(self, project_id: str) -> str:
        return self._get(f"/projects/{project_id}/report").text

    def artifact(self, project_id: str, kind: str) -> str | None:
        """Artifact text, or None when it doesn't exist for this project."""
        try:
            return self._get(
                f"/projects/{project_id}/artifacts/{kind}").text
        except DashboardApiError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise
