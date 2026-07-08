# Portfolio kit

## Resume bullets (pick 3–4)

- Built **LegacyLens**, an AI software-migration analysis platform
  (Python, FastAPI, Celery, Redis, SQLAlchemy, Neo4j, LangGraph, Groq):
  upload a legacy codebase → evidence-cited findings, explainable
  readiness/effort scores, and a phased migration roadmap; 173 tests at
  ~95% coverage.
- Designed a **hallucination-prevention architecture**: LLM agents see
  only a findings digest (never code) and pass a LangGraph
  citation-validator node that rejects any output referencing
  nonexistent finding IDs and feeds violations into the retry prompt.
- Implemented deterministic analysis across 3 ecosystems: tree-sitter
  AST import graphs (Python/Java/JS-TS), manifest parsing
  (pip/poetry/npm/maven/gradle), iterative-Kosaraju cycle detection and
  topological migration-wave ordering, and EOL/CVE enrichment from
  endoflife.date and OSV.dev with cached, offline-degrading clients.
- Shipped an async job platform — FastAPI (202 + job id), Celery/Redis
  workers, Postgres persistence, Streamlit dashboard, single-image
  Docker Compose — with security hardening: zip-bomb/zip-slip-safe
  ingestion and a tested invariant that no secret value ever appears in
  any report.

## LinkedIn launch post

---

For the last month I built the most ambitious project of my portfolio:
**LegacyLens — an AI Software Migration Platform.**

The problem: before modernizing a 10–20-year-old system, senior
architects spend weeks just understanding it — dependencies, dead code,
EOL frameworks, hidden coupling, migration order.

LegacyLens automates that analysis. Upload a codebase and get:
📊 a migration readiness score that explains every point it deducted
🔍 evidence-cited findings (EOL frameworks, CVEs, cycles, secrets, dead code)
🗺️ a wave-by-wave migration order computed from the import graph
📄 an executive report where every AI sentence cites a finding ID

The part I'm proudest of isn't the AI — it's the *constraint* on the AI.
The LLM never sees code. It sees findings produced by deterministic
analyzers (tree-sitter ASTs, manifest parsers, graph algorithms,
endoflife.date/OSV.dev lookups), and a LangGraph validator node rejects
any output citing evidence that doesn't exist. Hallucination prevention
as a pipeline stage, not a prompt request.

Stack: Python · FastAPI · Celery · Redis · SQLAlchemy · Neo4j ·
tree-sitter · LangGraph · Groq (Llama 3.3 70B) · Streamlit · Docker
173 tests, ~95% coverage.

Repo + full architecture writeup: <link>
I'm looking for GenAI Engineer roles — if this is the kind of engineering
your team values, my DMs are open.

---

## Interview defense cheat sheet

**"Walk me through the architecture."**
Facts layer → judgment layer. Deterministic analyzers (a plugin registry,
topologically ordered by declared dependencies) produce Findings that
cannot exist without Evidence — that's a Pydantic invariant, not a
convention. A scoring engine turns findings into capped, itemized
numbers. Only then do LangGraph agents produce prose, citation-gated
against the evidence store. FastAPI/Celery/Redis wrap it as an async job
platform; Streamlit is a pure HTTP client on top.

**"How do you prevent hallucination?"**
Three mechanisms, layered: (1) containment — agents receive a findings
digest, never code, so there's nothing to hallucinate *about*; (2)
validation — a graph node checks every cited ID (structured arrays AND
inline mentions) against the store, rejects, and retries with the exact
violation in the prompt; (3) division of labor — numbers come from
deterministic formulas the LLM only explains.

**"Why is the Celery task one line?"**
Because logic in a task is untestable without a broker. All orchestration
lives in `run_analysis()`, which takes session factory, settings, and LLM
as parameters — the full lifecycle (success and failure) tests against
tmp SQLite. Celery config is policy: acks_late so a killed worker
requeues, prefetch=1 because analyses are long, no auto-retries because
the function records its own failure state.

**"How do you compute migration order?"**
Import edges mean "depends on". Kosaraju SCC (iterative — recursion depth
is input-controlled on huge repos; tested on a 20k-node chain) collapses
cycles into single units, then topological depth over the condensation
gives waves: wave 0 has no internal dependencies, modules within a wave
migrate in parallel, cycles migrate as one unit or get broken first. The
tested invariant: every dependency sits in a strictly earlier wave.

**"What was the hardest bug?"**
My raw-SQL detector matched the English sentence "select a widget from
the menu". Fixed with uppercase-keyword matching and documented it as a
precision-over-recall trade-off: a false finding costs trust in every
true one. Also: Python's zipfile reads sizes from the central directory,
which taught me exactly which layer of my zip-bomb defense catches which
attack.

**"How would you scale it?"**
Workers are stateless and analyses embarrassingly parallel — add
replicas. Postgres for findings, artifact upserts idempotent, Neo4j
writes batched UNWIND+MERGE. Next bottleneck is per-analyzer file reads:
a shared content cache and SHA-256-keyed incremental re-analysis are on
the backlog. Observability is already correlation-id JSON logs.

**"What would you do differently?"**
Add the shared file cache from day one; add confidence scores to import
resolution (dotted-suffix matching can be ambiguous); introduce Alembic
before the schema grew; and wire LangSmith tracing into the agent graph
from Module 10 instead of the backlog.
