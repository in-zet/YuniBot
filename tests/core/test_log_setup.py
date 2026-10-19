from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from core.log_setup import configure_logging


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    for handler in root.handlers[:]:
        handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


def test_configure_logging_writes_records_to_a_file_under_the_given_dir(tmp_path: Path) -> None:
    path = configure_logging(tmp_path / "logs")

    logging.getLogger("core.agent").info("멘션 응답 종료")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert path == tmp_path / "logs" / "yuni.log"
    text = path.read_text(encoding="utf-8")
    assert "INFO:core.agent:멘션 응답 종료" in text
