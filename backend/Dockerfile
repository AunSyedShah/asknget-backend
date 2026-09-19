# syntax=docker/dockerfile:1.7
# asknget Django API image.
#
#   docker build -t asknget-api ./backend
#
# Two stages: `builder` resolves dependencies with uv into /app/.venv (cached
# between builds unless uv.lock changes); `runtime` copies only that venv plus
# the source, runs as a non-root user and serves with gunicorn.
#
# Base images come from Docker Hub: `astral/uv` (official uv image, Debian
# trixie + Python 3.13 + uv) and the matching `python` image. Keep the two
# Debian releases in sync so the venv built in one stage runs in the other.

ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.12
ARG DEBIAN_RELEASE=trixie

# --------------------------------------------------------------------------- #
FROM docker.io/astral/uv:${UV_VERSION}-python${PYTHON_VERSION}-${DEBIAN_RELEASE}-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first so this layer is reused when only source changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# --------------------------------------------------------------------------- #
FROM docker.io/library/python:${PYTHON_VERSION}-slim-${DEBIAN_RELEASE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=asknget.settings \
    WEB_CONCURRENCY=3 \
    GUNICORN_CMD_ARGS="--threads 2"

# postgresql-client gives the entrypoint `pg_isready`; curl is for the healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
# uv itself, so `docker compose exec web uv run|add|lock ...` works like on your PC.
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/
COPY --chown=app:app asknget ./asknget
COPY --chown=app:app docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /app/asknget/media /app/asknget/staticfiles \
    && chown -R app:app /app

# Static files (admin, Swagger UI) are baked into the image; served by whitenoise.
# A throwaway SECRET_KEY is enough for collectstatic.
RUN cd asknget && SECRET_KEY=build DEBUG=0 python manage.py collectstatic --noinput

USER app
WORKDIR /app/asknget
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
# Worker count comes from WEB_CONCURRENCY (gunicorn reads it natively); extra flags from GUNICORN_CMD_ARGS.
CMD ["gunicorn", "asknget.wsgi:application", "--bind", "0.0.0.0:8000", "--timeout", "60", "--access-logfile", "-"]
