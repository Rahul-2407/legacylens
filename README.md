# 🔍 LegacyLens

**An AI Software Migration Platform.** Upload a legacy codebase; get an
evidence-cited assessment: what it's built from, what's past end-of-life,
where the risk concentrates, what order to migrate in, and how much
effort that takes — as an interactive dashboard and an executive report.

> **Core principle:** deterministic static analysis produces facts; AI
> produces judgment grounded in those facts — never the reverse. A
> `Finding` without `Evidence` cannot be constructed (type-system
> invariant), LLM agents never see raw code, and a citation validator
> rejects any AI output referencing evidence that doesn't exist.

**173 tests · ~95% coverage · Python 3.12+**

## What it does

| Capability | How |
|---|---|
| Technology inventory | manifests (pip/poetry/npm/maven/gradle), build files, Dockerfile `FROM` lines |
| Module dependency graph | tree-sitter ASTs (Python, Java, JS/TS) + layout-agnostic import resolution |
| Migration order | Kosaraju SCC + topological waves: wave 0 first, cycles move as one unit |
| End-of-life detection | [endoflife.date](https://endoflife.date) citations — "Django 1.11 EOL 2020-04-01" |
| Known vulnerabilities | [OSV.dev](https://osv.dev) batch queries per pinned dependency, CVE-cited |
| Secrets & config risks | two-tier detection with a tested no-secret-in-any-report redaction invariant |
| Technical debt | test-safety-net ratio, dead files, god files, raw-SQL coupling, TODO density |
| Readiness & effort scores | capped, itemized formulas — the breakdown must sum to the score (tested) |
| AI narrative | LangGraph agents (analyst → strategist → writer) on Groq Llama 3.3, citation-gated with retry-on-violation |
| Deliverables | Streamlit dashboard, Markdown + Word reports, Mermaid diagrams, REST API |

18+ deterministic rules across 8 pluggable analyzers — adding an analyzer
is one class in one file.

## Quick start

```bash
# Library mode — analyze a repo in 30 seconds, no infrastructure:
pip install -e ".[dev]"
pytest                                    # 173 passed
python scripts/evidence_smoke.py          # live EOL/CVE lookups (internet)

# Full platform — API + worker + dashboard + Postgres + Redis:
export LEGACYLENS_GROQ_API_KEY=gsk_...    # optional; omit = deterministic-only
docker compose up --build
open http://localhost:8501                # dashboard
open http://localhost:8000/docs           # Swagger
```

## Architecture (short version)

```
upload ──► FastAPI (202 + job id) ──► Redis ──► Celery worker
                                                    │
     ingestion (zip-bomb/zip-slip hardened) ─► tree-sitter + manifest parsing
        ─► 8 analyzers (topo-ordered plugins) ─► findings w/ mandatory evidence
        ─► scoring (explainable formulas) ─► LangGraph agents (citation-gated)
        ─► report + diagrams ─► Postgres artifacts ─► dashboard / REST
```

Failure philosophy, uniform at every tier: record, degrade visibly,
continue. An analyzer crash skips its dependents with a reason; an
unreachable external API becomes an explicit "this report is a lower
bound" note; a failed LLM agent becomes a visible note while other
sections ship; a job can never be stuck `running`.

Full details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) ·
[module-by-module build log](docs/BUILDLOG.md)

## Project structure

```
src/legacylens/
├── core/         settings, JSON logging w/ correlation ids, exceptions
├── domain/       Finding / Evidence / ProjectContext (the invariants)
├── ingestion/    hardened extraction, inventory, language detection
├── parsing/      manifests (5 ecosystems) + tree-sitter AST layer
├── evidence/     endoflife.date + OSV.dev clients (cached, offline-degrading)
├── analyzers/    8 plugins, 18+ rules, dependency-ordered registry
├── graph/        SCC, waves, blast radius + Neo4j store
├── scoring/      readiness / complexity / risk / effort (explainable)
├── agents/       LangGraph analyst → strategist → writer, citation-gated
├── artifacts/    Mermaid + Markdown/docx report assembly
├── db/           SQLAlchemy models + repository
├── service/      run_analysis orchestration + Celery shell
└── api/          FastAPI service
dashboard/        Streamlit (pure API client)
```

## License

MIT
