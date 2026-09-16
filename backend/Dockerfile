# syntax=docker/dockerfile:1.7
# asknget Django API image.
#
#   docker build -t asknget-api ./backend
#
# Two stages: `builder` resolves dependencies with uv into /app/.venv (cached
# between builds unless uv.lock changes); `runtime` copies only that venv plus
# the source, runs as a non-root user and serves with gunicorn.

ARG PYTHON_VERSION=3.13

# --------------------------------------------------------------------------- #
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first so this layer is reused when only source changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# --------------------------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=asknget.settings

# postgresql-client gives the entrypoint `pg_isready`; curl is for the healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
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
CMD ["gunicorn", "asknget.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-"]
