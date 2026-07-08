# LegacyLens build log (module-by-module history)

> Preserved development history. For current docs see the main README,
> docs/ARCHITECTURE.md and docs/DEPLOYMENT.md.

# LegacyLens — AI Software Migration Platform

An enterprise platform that analyzes legacy software systems and produces
evidence-grounded modernization plans: architecture reports, dependency
graphs, risk matrices, and phased migration roadmaps.

**Core principle:** deterministic static analysis produces facts; AI produces
judgment grounded in those facts — never the reverse. A `Finding` cannot be
constructed without at least one `Evidence` record; the principle is enforced
by the type system, not by prompting.

## Architecture (build in progress)

| Layer | Technology | Status |
|---|---|---|
| Core foundation (domain models, analyzer contract, pipeline) | Python 3.12+, Pydantic v2 | ✅ Module 1 |
| Ingestion (hardened uploads, inventory, language detection) | stdlib, streaming caps | ✅ Module 2 |
| Manifest parsing (pip / poetry / npm / maven / gradle) | stdlib parsers | ✅ Module 3 |
| AST parsing + module graph (py / java / js / ts) | tree-sitter 0.23+ | ✅ Module 4 |
| External evidence clients (cached, resilient) | endoflife.date, OSV.dev, httpx | ✅ Module 5 |
| Tech detection, EOL, vulnerability analyzers | evidence-cited | ✅ Module 6 |
| Config secrets (redacted), database, tech-debt analyzers | deterministic rules | ✅ Module 8 |
| Graph algorithms + Neo4j store (cycles, waves, blast radius) | Kosaraju, graphlib, Neo4j | ✅ Module 7 |
| Scoring engine (readiness, complexity, risk, effort) | explainable formulas | ✅ Module 9 |
| AI synthesis (analyst → strategist → writer, citation-gated) | LangGraph + Groq | ✅ Module 10 |
| Report + diagram generation (md, docx, Mermaid) | deterministic assembly | ✅ Module 11 |
| Service layer (async jobs, persistence, REST API) | FastAPI, Celery, Redis, SQLAlchemy | ✅ Module 12 |
| Dashboard (projects, findings explorer, risk views) | Streamlit + Plotly | ✅ Module 13 |

## Module 1 — Core foundation

What exists now:

- `domain/models.py` — `Finding`, `Evidence`, `FileRecord`, `ProjectContext`.
  Findings require evidence (Pydantic-enforced invariant).
- `analyzers/base.py` — the `Analyzer` contract. Adding an analysis
  capability = one class in one file.
- `analyzers/registry.py` — self-registration, dependency closure, and
  topological execution ordering via stdlib `graphlib`; cycles and unknown
  dependencies fail fast.
- `pipeline/runner.py` — executes analyzers in order with per-analyzer
  timing, failure isolation, skip propagation to dependents, and provenance
  stamping on every finding.
- `core/` — settings (pydantic-settings), structured JSON logging with
  correlation IDs, exception hierarchy.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Writing an analyzer

```python
from legacylens import Analyzer, AnalyzerResult, registry
from legacylens.domain.models import Evidence, Finding, FindingCategory, Severity

@registry.register
class HelloAnalyzer(Analyzer):
    id = "hello"
    name = "Hello analyzer"
    depends_on = ()  # ids of analyzers that must run first

    def analyze(self, ctx):
        finding = Finding(
            analyzer_id=self.id,
            rule_id="HELLO-001",
            category=FindingCategory.TECHNOLOGY,
            severity=Severity.INFO,
            title="Project inventoried",
            description=f"Project contains {len(ctx.files)} files.",
            evidence=[Evidence(detail="file inventory", file_path=".")],
        )
        return AnalyzerResult(findings=[finding])
```

## Module 2 — Ingestion

- `ingestion/safety.py` — pre-extraction archive policy: zip-slip and
  absolute-path rejection, symlink-member rejection, size / file-count
  ceilings, compression-ratio bomb detection.
- `ingestion/extractor.py` — streaming extraction with a byte cap on bytes
  actually written (defense-in-depth against header manipulation) and
  cleanup-on-failure so half-extracted archives never linger.
- `ingestion/languages.py` — filename map + extension map + shebang sniffing.
- `ingestion/inventory.py` — single-pass SHA-256 + binary sniff per file;
  vendor/tooling directories (node_modules, .git, target, …) excluded.
- `ingestion/ingestor.py` — `ProjectIngestor.ingest_archive()` for uploads,
  `.ingest_directory()` for local repos; both return a ready ProjectContext.

```python
from legacylens import PipelineRunner, registry
from legacylens.ingestion.ingestor import ProjectIngestor

ctx = ProjectIngestor().ingest_archive(Path("upload.zip"))
result = PipelineRunner(registry).run(ctx)
```

## Module 3 — Manifest parsers + first real analyzer

- `parsing/manifests/` — five parsers (requirements.txt, pyproject.toml
  with PEP 621 + Poetry, package.json, pom.xml with property resolution,
  best-effort build.gradle) normalizing into one `DeclaredDependency` model.
- `analyzers/manifest_deps.py` — publishes a `DependencyInventory` artifact
  and emits rules MAN-PARSE-001 (unparseable manifest), DEP-UNPINNED-001
  (floating runtime versions, grouped per manifest, evidence capped at 20
  with true totals in metadata), DEP-DUP-001 (duplicate declarations).
- `analyzers/builtin.py` — explicit `load_builtin_analyzers()` so
  registration is a deliberate startup act, not an import accident.

## Module 4 — tree-sitter AST layer + module graph

- `parsing/ast/factory.py` — lazy cached parsers; tsx vs typescript grammar
  selection by extension.
- `parsing/ast/extract.py` — import extraction for Python (incl. relative
  levels and from-names), Java (incl. wildcard and static), JS/TS (import /
  export-from / require / dynamic import). Manual node walks, no query API.
- `parsing/ast/resolvers.py` — specifier → project file: Python dotted-suffix
  index (src-root agnostic) + relative handling; Java classpath suffix with
  static/inner-class fallback and wildcard → package dir; Node-style
  extension and /index probing. Unresolvable relative imports are broken=True.
- `analyzers/module_graph.py` — publishes the ModuleGraph artifact; rules
  AST-SYNTAX-001 (unparseable sources, analysis continues on recovered
  trees) and AST-IMPORT-001 (imports referencing missing files — dead code
  or broken builds, HIGH severity).

Dogfood check: running the pipeline on LegacyLens itself parses 40 sources,
resolves 86 internal edges with 0 broken, and correctly flags its own
unpinned `>=` dependencies.

## Module 5 — External evidence clients

- `evidence/http.py` — one resilience policy for all clients: hard timeout,
  bounded retries with backoff on 5xx/429/network errors, fail-fast on 4xx,
  404 = valid None answer, offline_mode short-circuit before any socket.
  Transport-injectable for MockTransport testing.
- `evidence/cache.py` — file cache with TTL; corrupted entries degrade to a
  miss. Negative results ("product unknown") are cached too.
- `evidence/eol.py` — endoflife.date cycles with polymorphic `eol` handling
  (date / true / false) and honest tristate `is_eol()`.
- `evidence/osv.py` — OSV.dev single query (full details) and querybatch
  (ids only, the cheap shape for 300-dependency projects). Severity is
  surfaced exactly as published, never computed.

Live smoke (run on your machine, not required for tests):
```bash
python scripts/evidence_smoke.py
```

## Module 6 — Intelligence analyzers

- `analyzers/tech_detection.py` — TechnologyProfile artifact from three
  sources: dependency-name mapping table (also carries endoflife.date
  product keys — wrong keys degrade to a safe skip via 404→None), build
  files, and Dockerfile FROM lines (FROM python:2.7 is prime legacy
  evidence, captured with file + line).
- `analyzers/tech_eol.py` — TECH-EOL-001 (past EOL; CRITICAL when ≥3 years
  past), TECH-EOL-002 (EOL within 12 months). Dual evidence on every
  finding: the manifest/Dockerfile line + the endoflife.date citation URL.
- `analyzers/dep_vulns.py` — DEP-VULN-001 from OSV.dev, batch-first for
  API politeness; severity = worst published label, MEDIUM when advisories
  exist but severity is unpublished. Evidence: manifest line + per-CVE
  citation URLs (capped at 10, full ids in metadata).
- Shared EVID-UNAVAILABLE-001: network failure never crashes or silences
  analysis — the report states which enrichment is a lower bound.

## Module 7 — Dependency graph layer

- `graph/algorithms.py` — pure functions: iterative Kosaraju SCC (no
  recursion — depth is input-controlled on legacy repos), migration waves
  via condensation + topological depth (wave 0 first; invariant tested:
  every dependency sits in a strictly earlier wave; cycles move as one
  unit), fan-in/fan-out metrics, blast radius (transitive importers).
- `graph/store.py` — thin Neo4j writer: batched idempotent UNWIND+MERGE
  keyed by (project_id, path), computed properties persisted for the
  dashboard; injectable driver (unit-tested via recording stub); optional
  extra `pip install 'legacylens[graph]'`.
- `analyzers/architecture.py` — ARCH-CYCLE-001 (HIGH, evidence = the
  actual import lines forming the cycle), ARCH-HOTSPOT-001 (fan-in ≥ 10),
  ARCH-WAVES-001 (wave structure summary). Publishes ArchitectureReport,
  the migration planner's raw material.

## Module 8 — Config, database, and debt analyzers

- `analyzers/config_analysis.py` — two-tier secret detection: key=value
  heuristic in config-language files only; high-confidence patterns (AWS
  key ids, private-key headers, user:pass@ URLs) everywhere. REDACTION
  INVARIANT: no secret value appears anywhere in any finding — tested over
  the entire serialized finding JSON. Template files (.example/.sample)
  skipped; committed .env files flagged (CONF-ENV-001).
- `analyzers/db_analysis.py` — engines from drivers + connection strings
  (dual evidence sources); raw-SQL coupling counts (uppercase-keyword
  matching, a documented prose-false-positive trade-off).
- `analyzers/tech_debt.py` — DEBT-TESTS-001 (test-to-source ratio: the
  migration safety-net signal, HIGH when near zero), DEBT-TODO-001,
  DEBT-DEAD-001 (zero-importer files minus entrypoint heuristics: delete
  before migrating), DEBT-LARGE-001 (god files block incremental phases).

## Module 9 — Scoring engine

- `scoring/engine.py` — `compute_scorecard(findings, ctx)`: a
  post-pipeline synthesis stage (not an analyzer — it needs the complete
  finding set). All weights in one visible policy table.
- Readiness score: per-severity deductions with per-severity caps and a
  zero floor; ships its component breakdown, and a test asserts the
  breakdown sums exactly to the score (explainability enforced).
- Module complexity: 0.35·size + 0.35·coupling + 0.20·finding-load +
  0.10·cycle-membership, banded low/medium/high, components exposed.
- Effort: band-based person-days × test-safety-net multiplier + 15%
  integration overhead, ±30% spread — with the assumptions embedded in
  the artifact ("a heuristic model, not a quote").
- Selection: quick wins (wave-0, low-band, no severe findings) and
  high-risk modules with per-module reasons.
- LLM agents (Module 10) explain these numbers; they never produce them.

## Module 10 — LangGraph synthesis engine

- `agents/digest.py` — the ONLY project view agents receive: findings with
  IDs + key evidence, scorecard breakdowns, wave structure. Deterministic
  ordering; agents never see raw code (the hallucination boundary).
- `agents/citation.py` — the gate: schema-validated JSON sections, every
  section must cite ≥1 real finding ID, and inline F-xxxx mentions in
  prose are checked too (no smuggled invented references).
- `agents/graph.py` — LangGraph StateGraph: analyst → validate ↺ retry →
  strategist → validate ↺ retry → writer. Rejections feed the exact
  violations into the retry prompt; after max retries the section is
  marked failed and the graph continues (partial results, like the
  pipeline runner). LLM transport errors degrade the same way.
- `agents/llm.py` — one-method LlmClient protocol; GroqClient over httpx
  (json_object response format). Tests inject a scripted fake; provider
  swap = one small class.
- `agents/prompts.py` — a shared grounding contract ("the digest is your
  entire universe… omission is correct; invention is failure") + three
  role prompts.

Set `LEGACYLENS_GROQ_API_KEY` to run live synthesis; everything else
works without it.

## Module 11 — Artifact generation

- `artifacts/mermaid.py` — diagrams rendered deterministically from graph
  data, never through the LLM. Module graph capped to top-coupling nodes
  with an honest "+N omitted" legend; cycle members styled; migration
  waves as ordered subgraphs.
- `artifacts/report.py` — the assembler: machine tables (readiness
  breakdown, risk matrix, effort with assumptions, findings appendix)
  interleaved with agents' cited prose, [F-...] citations kept intact and
  resolvable in the appendix. Failed/absent synthesis becomes a visible
  note — the deterministic report is complete either way. Word export via
  python-docx for the executive deliverable.

## Module 12 — Service layer

- `db/` — SQLAlchemy 2.0: projects (lifecycle status), findings (severity/
  rule as filterable columns, evidence as JSON), artifacts ((project, kind)
  upsert — new artifact types need no migration). Same code on SQLite (dev)
  and Postgres (prod).
- `service/analysis.py` — run_analysis(): the plain function every entry
  point delegates to; all dependencies injected. Jobs can never be stuck
  'running' — any exception records 'failed' with the reason. LLM absence
  degrades; it is never a job failure.
- `service/tasks.py` — the thinnest file in the project: Celery app
  (acks_late, prefetch=1, no auto-retries) + a one-line task shell.
- `api/app.py` — POST /projects (streamed upload w/ size cap) → 202 +
  job id; status, findings (severity filter), scorecard, markdown report
  (409 until completed). create_app() injects session factory + enqueue.

Run it:
```bash
uvicorn "legacylens.api.app:create_app" --factory          # API
celery -A legacylens.service.tasks worker --loglevel=info  # worker (needs Redis)
```

## Module 13 — Streamlit dashboard

- Pure API client (`dashboard/api_client.py`) — the UI never imports the
  analysis engine; it is the swappable presentation layer promised in the
  architecture. Injectable transport for tests.
- Projects page: zip upload → queued analysis; portfolio table with
  readiness progress bars and status.
- Project detail: readiness metric with "why this score" breakdown, risk
  matrix heatmap (Plotly), findings explorer with severity/category
  filters and per-finding evidence drill-down, Mermaid architecture and
  wave diagrams (rendered once at analysis time, served as artifacts),
  markdown report preview + download.
- Tested three ways: ApiClient over MockTransport, pure helpers, and
  Streamlit AppTest smoke tests driving both pages with a fake client.

Run: `streamlit run dashboard/app.py` (install extras:
`pip install -e '.[dashboard]'`)
