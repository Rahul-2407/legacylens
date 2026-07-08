"""FastAPI service.

The async job contract:

    POST /projects            multipart zip -> 202 {project_id, status}
    GET  /projects            recent projects
    GET  /projects/{id}       lifecycle status + summary
    GET  /projects/{id}/findings?severity=high
    GET  /projects/{id}/scorecard
    GET  /projects/{id}/report        (markdown; 409 until completed)
    GET  /health

Dependencies (session factory, enqueue function, settings) are injected
via create_app() so tests run the real HTTP surface with tmp SQLite and a
synchronous enqueue — no Redis required to prove the API correct.
"""

import json
import uuid
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from legacylens.core.config import Settings, get_settings
from legacylens.db import session as repo
from legacylens.db.session import make_session_factory

_CHUNK = 1024 * 1024


class ProjectOut(BaseModel):
    project_id: str
    name: str
    status: str
    file_count: int | None = None
    readiness: int | None = None
    error: str | None = None


class FindingOut(BaseModel):
    finding_id: str
    rule_id: str
    analyzer_id: str
    category: str
    severity: str
    title: str
    description: str
    evidence: list
    metadata: dict


def _to_project_out(record) -> ProjectOut:
    return ProjectOut(
        project_id=record.project_id, name=record.name,
        status=record.status, file_count=record.file_count,
        readiness=record.readiness, error=record.error,
    )


def create_app(
    settings: Settings | None = None,
    session_factory=None,
    enqueue: Callable[[str, str], None] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    session_factory = session_factory or make_session_factory(settings)
    if enqueue is None:
        from legacylens.service.tasks import enqueue_analysis
        enqueue = enqueue_analysis

    app = FastAPI(
        title="LegacyLens",
        description="Evidence-grounded software migration analysis",
        version="0.1.0",
    )
    uploads_dir = (settings.workspace_dir.expanduser().resolve()
                   / "_uploads")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/projects", status_code=202, response_model=ProjectOut)
    async def create_project(file: UploadFile):
        if not (file.filename or "").lower().endswith(".zip"):
            raise HTTPException(400, "Upload must be a .zip archive")
        project_id = f"proj-{uuid.uuid4().hex[:12]}"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        target = uploads_dir / f"{project_id}.zip"
        limit = settings.max_archive_size_mb * 1024 * 1024
        written = 0
        with open(target, "wb") as out:
            while chunk := await file.read(_CHUNK):
                written += len(chunk)
                if written > limit:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(
                        413, f"Archive exceeds {settings.max_archive_size_mb} MB")
                out.write(chunk)

        with session_factory() as session:
            record = repo.create_project(session, project_id,
                                         name=file.filename)
        enqueue(project_id, str(target))
        return _to_project_out(record)

    @app.get("/projects", response_model=list[ProjectOut])
    def list_projects():
        with session_factory() as session:
            return [_to_project_out(r) for r in repo.list_projects(session)]

    @app.get("/projects/{project_id}", response_model=ProjectOut)
    def get_project(project_id: str):
        with session_factory() as session:
            record = repo.get_project(session, project_id)
        if record is None:
            raise HTTPException(404, "Unknown project")
        return _to_project_out(record)

    @app.delete("/projects/{project_id}", status_code=204)
    def delete_project(project_id: str):
        with session_factory() as session:
            existed = repo.delete_project(session, project_id)
        if not existed:
            raise HTTPException(404, "Unknown project")
        # best-effort removal of the uploaded archive
        archive = uploads_dir / f"{project_id}.zip"
        archive.unlink(missing_ok=True)
        return None

    @app.get("/projects/{project_id}/findings",
             response_model=list[FindingOut])
    def get_findings(project_id: str, severity: str | None = None,
                     limit: int = 500):
        with session_factory() as session:
            if repo.get_project(session, project_id) is None:
                raise HTTPException(404, "Unknown project")
            rows = repo.get_findings(session, project_id,
                                     severity=severity, limit=limit)
        return [FindingOut(
            finding_id=r.finding_id, rule_id=r.rule_id,
            analyzer_id=r.analyzer_id, category=r.category,
            severity=r.severity, title=r.title,
            description=r.description, evidence=r.evidence,
            metadata=r.meta,
        ) for r in rows]

    @app.get("/projects/{project_id}/scorecard")
    def get_scorecard(project_id: str):
        payload = _completed_artifact(session_factory, project_id,
                                      "scorecard")
        return json.loads(payload)

    ARTIFACT_KINDS = {"report_md", "scorecard", "synthesis",
                      "mermaid_modules", "mermaid_waves"}

    @app.get("/projects/{project_id}/artifacts/{kind}",
             response_class=PlainTextResponse)
    def get_artifact(project_id: str, kind: str):
        if kind not in ARTIFACT_KINDS:
            raise HTTPException(404, f"Unknown artifact kind '{kind}'")
        return PlainTextResponse(
            _completed_artifact(session_factory, project_id, kind))

    @app.get("/projects/{project_id}/report",
             response_class=PlainTextResponse)
    def get_report(project_id: str):
        return PlainTextResponse(
            _completed_artifact(session_factory, project_id, "report_md"),
            media_type="text/markdown",
        )

    return app


def _completed_artifact(session_factory, project_id: str, kind: str) -> str:
    with session_factory() as session:
        record = repo.get_project(session, project_id)
        if record is None:
            raise HTTPException(404, "Unknown project")
        if record.status != "completed":
            raise HTTPException(
                409, f"Analysis is '{record.status}'; artifact available "
                     "once completed")
        payload = repo.get_artifact(session, project_id, kind)
    if payload is None:
        raise HTTPException(404, f"No '{kind}' artifact for this project")
    return payload


app = create_app  # uvicorn: `uvicorn legacylens.api.app:create_app --factory`
