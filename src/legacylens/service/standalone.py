"""In-process analysis runner for single-process deployments.

Streamlit Community Cloud (and similar single-container hosts like HF
Spaces) run exactly one Python process — there's no separate Celery
worker and no Redis broker to enqueue onto. `run_analysis` in
`service.analysis` was already written as a plain function with every
dependency injected (that's what lets the test suite run it against tmp
SQLite with no infrastructure), so standalone mode just needs an
`enqueue` callable with the same signature as the Celery task that runs
the job on a background thread instead of a queue.

This does NOT touch the Celery/Redis path used by docker-compose — it's
an additional option, selected by whichever `enqueue` you pass to
`create_app()`.
"""

import threading

from legacylens.core.config import Settings, get_settings
from legacylens.db.session import make_session_factory
from legacylens.service.analysis import run_analysis


def make_sync_enqueue(settings: Settings | None = None):
    """Return an `enqueue(project_id, archive_path)` that runs the job on
    a background thread against a shared SQLite-backed session factory.

    One session factory is created here and reused for every job, mirroring
    how the API and worker share one engine per process today.
    """
    settings = settings or get_settings()
    session_factory = make_session_factory(settings)

    def enqueue(project_id: str, archive_path: str) -> None:
        thread = threading.Thread(
            target=run_analysis,
            args=(project_id, archive_path, session_factory, settings),
            daemon=True,
        )
        thread.start()

    return enqueue
