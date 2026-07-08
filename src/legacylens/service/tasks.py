"""Celery wiring — deliberately the thinnest file in the project.

The task body is one delegation to run_analysis(); all logic lives there
and is tested without a broker. Worker command:

    celery -A legacylens.service.tasks worker --loglevel=info
"""

from celery import Celery

from legacylens.core.config import get_settings


def create_celery() -> Celery:
    settings = get_settings()
    app = Celery(
        "legacylens",
        broker=settings.redis_url,
        backend=settings.redis_url,
    )
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_acks_late=True,             # a killed worker requeues the job
        worker_prefetch_multiplier=1,    # analyses are long; don't hoard
        task_track_started=True,
    )
    return app


celery_app = create_celery()


@celery_app.task(name="legacylens.analyze_project", bind=True,
                 max_retries=0)
def analyze_project(self, project_id: str, archive_path: str) -> None:
    """No Celery-level retries: run_analysis records its own failure state,
    and re-running a failed analysis is a user decision, not a loop."""
    from legacylens.db.session import make_session_factory
    from legacylens.service.analysis import run_analysis

    run_analysis(project_id, archive_path,
                 make_session_factory(get_settings()))


def enqueue_analysis(project_id: str, archive_path: str) -> None:
    """Default enqueue used by the API; injectable in tests."""
    analyze_project.delay(project_id, archive_path)
