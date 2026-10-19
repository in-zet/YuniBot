# core/log_setup.py
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)s:%(name)s:%(message)s"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5  # 현재 파일 + 5개 = 최대 약 30MB


def configure_logging(log_dir: Path) -> Path:
    """표준 출력(docker logs)과 파일에 같이 남긴다. `docker compose up --build`로 컨테이너가
    다시 만들어지면 docker logs는 사라지므로(2026-10-19 실기) 로그 파일은 볼륨(`data/`) 안에 둔다.
    파일은 5MB마다 돌려 쓴다. 로그 파일 경로를 돌려준다."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "yuni.log"
    formatter = logging.Formatter(_LOG_FORMAT)
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    file_handler = RotatingFileHandler(path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[stream, file_handler], force=True)
    return path
