"""Persistence models (SQLAlchemy 2.0 typed style).

Three tables, deliberately few:

* projects   — one row per analysis job with its lifecycle status
* findings   — flattened for queryability (severity/rule/category are
               columns you can filter and index), with evidence and
               metadata as JSON payloads
* artifacts  — everything else (report markdown, scorecard JSON, synthesis
               JSON) as (project_id, kind) keyed payloads; adding a new
               artifact type is a new kind string, not a migration

JSON columns work identically on SQLite (dev) and Postgres (prod), which
is what makes the same code honest in both environments.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ProjectRecord(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # pending -> running -> completed | failed
    error: Mapped[str | None] = mapped_column(Text, default=None)
    file_count: Mapped[int | None] = mapped_column(default=None)
    readiness: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FindingRecord(Base):
    __tablename__ = "findings"
    __table_args__ = (
        Index("ix_findings_project_severity", "project_id", "severity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    finding_id: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id"), index=True)
    analyzer_id: Mapped[str] = mapped_column(String(64))
    rule_id: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class ArtifactRecord(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifacts_project_kind", "project_id", "kind",
              unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id"))
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow)
