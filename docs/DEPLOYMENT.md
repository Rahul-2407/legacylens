# Deployment

## Local development (no infrastructure)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,dashboard]"
pytest                                   # 173 tests

# Analyze a repo directly (no API/queue needed):
python - <<'PY'
from pathlib import Path
from legacylens import PipelineRunner, registry
from legacylens.analyzers.builtin import load_builtin_analyzers
from legacylens.ingestion.ingestor import ProjectIngestor
from legacylens.scoring.engine import compute_scorecard
from legacylens.artifacts.report import ReportBuilder

load_builtin_analyzers()
ctx = ProjectIngestor().ingest_directory(Path("~/code/some-repo").expanduser())
result = PipelineRunner(registry).run(ctx)
card = compute_scorecard(result.findings, ctx)
Path("report.md").write_text(
    ReportBuilder().build_markdown(ctx, result.findings, card, None))
PY
```

## Full stack (Docker Compose)

```bash
export LEGACYLENS_GROQ_API_KEY=gsk_...    # optional; omit for deterministic-only
docker compose up --build                  # api, worker, dashboard, postgres, redis
docker compose --profile graph up --build  # ...plus Neo4j
```

- Dashboard: http://localhost:8501 · API/Swagger: http://localhost:8000/docs
- Uploads land on the shared `appdata` volume so the worker container can
  read what the API container wrote.
- Postgres/Redis are healthchecked; api and worker wait for healthy.

Smoke test:

```bash
curl -s http://localhost:8000/health
curl -s -F "file=@sample.zip" http://localhost:8000/projects   # -> 202 + id
curl -s http://localhost:8000/projects/<id>                    # poll status
curl -s http://localhost:8000/projects/<id>/report             # markdown
```

## Configuration

All settings are `LEGACYLENS_*` environment variables (pydantic-settings;
see `core/config.py`): `DATABASE_URL`, `REDIS_URL`, `GROQ_API_KEY`,
`OFFLINE_MODE`, `MAX_ARCHIVE_SIZE_MB`, `NEO4J_*`, etc. Secrets are never
committed; `.env.example` documents the shape.

## Streamlit Community Cloud (single-process demo)

Streamlit Community Cloud only runs one process — no separate API
container, no Postgres, no Redis/Celery worker. `dashboard/bootstrap.py`
makes this work anyway: when the dashboard doesn't find an external
`LEGACYLENS_API_URL`, it starts the real FastAPI app in a background
thread inside the same process, wired to a threaded job runner
(`service/standalone.py`) instead of Celery, backed by SQLite (already
the default `LEGACYLENS_DATABASE_URL`). Nothing in the API, the pipeline,
or the dashboard views changes — the dashboard still only talks HTTP.

To deploy:

1. Push this repo to GitHub.
2. On https://share.streamlit.io, create a new app from the repo.
3. **Main file path: `streamlit_app.py`** (not `dashboard/app.py` — this
   thin wrapper puts the repo root on `sys.path` so `dashboard.*` imports
   resolve; Streamlit Cloud doesn't do this automatically the way
   `PYTHONPATH=/app` does in docker-compose).
4. Python version: 3.12 (set in the app's Advanced settings if offered).
5. Optional: add a secret `LEGACYLENS_GROQ_API_KEY = "gsk_..."` in the
   app's Secrets to get AI-synthesized executive summaries. Without it,
   the deterministic report still ships in full — synthesis is optional
   by design (see `service/analysis.py::_try_synthesis`).

Trade-offs versus the full stack, worth knowing for a portfolio writeup:

- **No Neo4j.** It's an optional extra the core pipeline never imports;
  the module/wave Mermaid diagrams still render (they're built from the
  in-memory `module_graph`/`architecture` artifacts at analysis time, not
  a live graph database — see `service/analysis.py::_diagrams`).
- **Ephemeral storage.** SQLite and uploaded archives live on the
  container's local disk, which is wiped on redeploy/restart. Fine for a
  live demo; not a substitute for the Postgres-backed persistence the
  Docker Compose stack gives you.
- **Unbounded thread concurrency**, since each upload spawns its own
  thread rather than being limited by Celery's `--concurrency`.
  Acceptable for demo traffic, not for production load — that's exactly
  the boundary `docs/ARCHITECTURE.md` already draws between "12-factor
  shape" and "cheapest live demo."

If you'd rather deploy the full stack and point a Streamlit-Cloud-hosted
dashboard at it (closer to the real architecture, still shows the
FastAPI/Postgres/Redis/Celery layer in your portfolio), deploy `api` +
`worker` + Postgres + Redis on Fly.io/Render/Railway per the section
below, then set `LEGACYLENS_API_URL` to that API's public URL as a
Streamlit Cloud secret — `bootstrap.py` steps aside automatically
whenever that variable is already set.

## Cloud notes

The stack is a standard 12-factor shape: one stateless API container, N
worker containers, managed Postgres + Redis. Reasonable targets:

- **Single VM** (simplest): docker compose behind Caddy/nginx with TLS.
- **AWS**: ECS/Fargate services from the same image (command per
  service), RDS Postgres, ElastiCache Redis, EFS or S3-backed uploads.
- **Fly.io / Render / Railway**: two processes (web + worker) + managed
  Postgres/Redis add-ons — the cheapest live demo for a portfolio.

Scaling knob: worker `--concurrency` and replica count; analyses are
CPU-and-I/O bound, embarrassingly parallel across projects. Observability:
every log line is single-line JSON with a `correlation_id` equal to the
project id — pipe straight into CloudWatch/Loki and filter one job's
entire lifecycle.
