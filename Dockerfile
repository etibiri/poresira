# SPDX-License-Identifier: AGPL-3.0-or-later
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

ARG PORESIRA_VERSION=dev
ENV PORESIRA_VERSION=${PORESIRA_VERSION} \
    DJANGO_SETTINGS_MODULE=config.settings.prod

RUN DJANGO_SECRET_KEY=build-only python manage.py compilemessages \
    && DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && useradd --system --no-create-home poresira
USER poresira

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
