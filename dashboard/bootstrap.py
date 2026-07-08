"""Standalone-mode bootstrap for the dashboard.

The dashboard's only contract is HTTP against `LEGACYLENS_API_URL`
(see `api_client.py`) — it never imports the analysis engine directly.
That boundary is what makes this file possible: when no external API is
configured, we start the *real* FastAPI app in a background thread inside
the same process, wired to a synchronous (threaded) job runner instead of
Celery/Redis, and point the dashboard at it over localhost. Nothing in
`dashboard/app.py` or the views needs to know the difference.

This activates automatically. If `LEGACYLENS_API_URL` is already set
(e.g. you deployed the API separately and pointed the dashboard at it),
this is a no-op and that URL is used instead — see DEPLOYMENT.md.
"""

import os
import threading

import streamlit as st

_DEFAULT_PORT = 8765


def _read_secret(name: str) -> str | None:
    try:
        value = st.secrets.get(name)
        return str(value) if value is not None else None
    except Exception:
        return None


@st.cache_resource(show_spinner=False)
def _start_server(port: int) -> bool:
    """Start uvicorn serving the real FastAPI app on a background thread.

    Guarded by st.cache_resource so it runs at most once per server
    process, no matter how many times Streamlit reruns this script.
    """
    import uvicorn

    from legacylens.api.app import create_app
    from legacylens.core.config import get_settings
    from legacylens.service.standalone import make_sync_enqueue

    settings = get_settings()
    app = create_app(settings=settings, enqueue=make_sync_enqueue(settings))

    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return True


def ensure_standalone_api(port: int = _DEFAULT_PORT) -> None:
    """Point the dashboard at a locally-started API if none is configured.

    Call this before `ApiClient()` is constructed (i.e. at the very top
    of dashboard/app.py) so the env var it reads is already in place.
    """
    if os.environ.get("LEGACYLENS_API_URL"):
        return  # dashboard is pointed at an external/deployed API already

    # Streamlit secrets aren't exposed as env vars automatically for
    # pydantic-settings to pick up, so bridge the one that matters.
    groq_key = os.environ.get("LEGACYLENS_GROQ_API_KEY") or _read_secret(
        "LEGACYLENS_GROQ_API_KEY")
    if groq_key:
        os.environ["LEGACYLENS_GROQ_API_KEY"] = groq_key

    os.environ["LEGACYLENS_API_URL"] = f"http://127.0.0.1:{port}"
    _start_server(port)
