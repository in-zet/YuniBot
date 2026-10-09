FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY . .
RUN uv sync --locked --no-dev

ENV PYTHONPATH=/app/third_party

# 1단계 placeholder: 2단계에서 core/의 실제 엔트리포인트로 바꾼다.
CMD ["sleep", "infinity"]
