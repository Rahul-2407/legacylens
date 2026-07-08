# LegacyLens — one image, three services (api / worker / dashboard).
# The command differs per service in docker-compose.yml; sharing the image
# keeps builds fast and guarantees api and worker run identical code.

# ---------------------------------------------------------------- builder
FROM python:3.12-slim AS builder

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
COPY dashboard ./dashboard
RUN pip install --no-cache-dir ".[graph,dashboard,postgres]"

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim

RUN useradd --create-home --uid 10001 legacylens
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    LEGACYLENS_WORKSPACE_DIR=/data/workspace \
    LEGACYLENS_CACHE_DIR=/data/cache

WORKDIR /app
COPY --chown=legacylens:legacylens src ./src
COPY --chown=legacylens:legacylens dashboard ./dashboard
RUN mkdir -p /data/workspace /data/cache && \
    chown -R legacylens:legacylens /data

USER legacylens
EXPOSE 8000 8501

# Default: the API. Worker and dashboard override `command` in compose.
CMD ["uvicorn", "legacylens.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
