FROM python:3.14-slim

# tzdata: docker-compose.yml의 TZ=Asia/Tokyo가 실제로 먹게 한다(세션 날짜 배정과 예약 작업이
# 로컬 벽시계 기준이다). 공식 python slim 이미지에도 들어 있지만, 베이스 이미지 사정에 기대지 않게 명시한다.
RUN apt-get update && apt-get install -y --no-install-recommends git tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY . .
RUN uv sync --locked --no-dev

ENV PYTHONPATH=/app/third_party

CMD ["uv", "run", "python", "-m", "core.bot"]
