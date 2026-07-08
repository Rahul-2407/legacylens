"""Central application settings.

All configuration is read from environment variables prefixed LEGACYLENS_
(or a local .env file), never hardcoded. Later modules extend this class
with their own sections (database URLs, broker URL, LLM keys) so there is
exactly one place configuration lives.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEGACYLENS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "LegacyLens"
    environment: str = "development"  # development | staging | production
    log_level: str = "INFO"

    # Workspace where uploaded projects are extracted and analyzed.
    workspace_dir: Path = Path("./workspace")

    # Hard ceilings applied during ingestion (Module 2) — defined here so the
    # safety limits are visible and configurable from day one.
    max_archive_size_mb: int = 500
    max_extracted_size_mb: int = 2000
    max_file_count: int = 50_000
    bomb_compression_ratio_limit: int = 150

    # External evidence clients (endoflife.date, OSV.dev)
    offline_mode: bool = False
    cache_dir: Path = Path("./.legacylens-cache")
    evidence_cache_ttl_hours: int = 24
    http_timeout_seconds: float = 10.0
    http_max_retries: int = 2

    # Neo4j (optional; used by the graph store and dashboard)
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "legacylens"
    neo4j_database: str = "neo4j"

    # LLM synthesis (Groq)
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    llm_temperature: float = 0.2
    synthesis_max_retries: int = 2

    # Service layer
    database_url: str = "sqlite:///./legacylens.db"
    redis_url: str = "redis://localhost:6379/0"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
