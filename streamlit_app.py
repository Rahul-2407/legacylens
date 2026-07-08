"""Entry point for Streamlit Community Cloud.

Set this file (`streamlit_app.py`) as the app's main file path when
deploying. It exists only to put the repo root on `sys.path` — Streamlit
Cloud runs `streamlit run <main file>` without adding the repo root to
the path the way `PYTHONPATH=/app` does in docker-compose, and
`dashboard/app.py` imports `dashboard.*` as a package. Everything else
(the actual app) lives in `dashboard/app.py`, unchanged.
"""

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

runpy.run_path(str(ROOT / "dashboard" / "app.py"), run_name="__main__")
