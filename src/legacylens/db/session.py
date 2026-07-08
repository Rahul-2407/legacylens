"""Session management and the repository.

Repository functions take a Session as their first argument — no hidden
globals — so the same functions serve the API (request-scoped session),
the worker (task-scoped session), and tests (tmp SQLite).
"""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from legacylens.core.config import Settings
from legacylens.db.models import (
    ArtifactRecord,
    Base,
    FindingRecord,
    ProjectRecord,
)
from legacylens.domain.models import Finding


def make_session_factory(settings: Settings) -> sessionmaker:
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False}
        if settings.database_url.startswith("sqlite") else {},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


# ------------------------------------------------------------- projects

def create_project(session: Session, project_id: str,
                   name: str) -> ProjectRecord:
    record = ProjectRecord(project_id=project_id, name=name,
                           status="pending")
    session.add(record)
    session.commit()
    return record


def set_status(session: Session, project_id: str, status: str,
               error: str | None = None,
               file_count: int | None = None,
               readiness: int | None = None) -> None:
    record = session.get(ProjectRecord, project_id)
    if record is None:
        return
    record.status = status
    if error is not None:
        record.error = error[:2000]
    if file_count is not None:
        record.file_count = file_count
    if readiness is not None:
        record.readiness = readiness
    session.commit()


def get_project(session: Session, project_id: str) -> ProjectRecord | None:
    return session.get(ProjectRecord, project_id)


def list_projects(session: Session, limit: int = 50) -> list[ProjectRecord]:
    stmt = (select(ProjectRecord)
            .order_by(ProjectRecord.created_at.desc()).limit(limit))
    return list(session.scalars(stmt))


# ------------------------------------------------------------- findings

def save_findings(session: Session, project_id: str,
                  findings: list[Finding]) -> None:
    session.add_all(FindingRecord(
        finding_id=f.finding_id,
        project_id=project_id,
        analyzer_id=f.analyzer_id,
        rule_id=f.rule_id,
        category=str(f.category),
        severity=str(f.severity),
        title=f.title,
        description=f.description,
        evidence=[e.model_dump(mode="json") for e in f.evidence],
        meta=f.metadata,
    ) for f in findings)
    session.commit()


def get_findings(session: Session, project_id: str,
                 severity: str | None = None,
                 limit: int = 500) -> list[FindingRecord]:
    stmt = select(FindingRecord).where(
        FindingRecord.project_id == project_id)
    if severity:
        stmt = stmt.where(FindingRecord.severity == severity)
    return list(session.scalars(stmt.limit(limit)))


def delete_project(session: Session, project_id: str) -> bool:
    """Delete a project and all of its findings and artifacts.

    Returns True if the project existed. Children are removed first so the
    delete works regardless of FK cascade configuration (SQLite in dev does
    not enforce cascades by default)."""
    record = session.get(ProjectRecord, project_id)
    if record is None:
        return False
    session.query(FindingRecord).filter(
        FindingRecord.project_id == project_id).delete()
    session.query(ArtifactRecord).filter(
        ArtifactRecord.project_id == project_id).delete()
    session.delete(record)
    session.commit()
    return True


# ------------------------------------------------------------ artifacts

def save_artifact(session: Session, project_id: str, kind: str,
                  payload: str) -> None:
    existing = session.scalar(
        select(ArtifactRecord)
        .where(ArtifactRecord.project_id == project_id,
               ArtifactRecord.kind == kind))
    if existing:
        existing.payload = payload      # re-analysis overwrites in place
    else:
        session.add(ArtifactRecord(project_id=project_id, kind=kind,
                                   payload=payload))
    session.commit()


def get_artifact(session: Session, project_id: str,
                 kind: str) -> str | None:
    record = session.scalar(
        select(ArtifactRecord)
        .where(ArtifactRecord.project_id == project_id,
               ArtifactRecord.kind == kind))
    return record.payload if record else None
