"""Shared pytest fixtures."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
CORPUS = Path(__file__).resolve().parent.parent / "corpus"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(CORPUS))

from chessbook.config import Settings  # noqa: E402
from chessbook.db import init_db  # noqa: E402


def pytest_configure(config):  # noqa: ARG001
    config.addinivalue_line("markers", "slow: needs OCR or the full corpus")


@pytest.fixture(scope="session")
def corpus_pdf() -> Path:
    return CORPUS / "pdf"


@pytest.fixture(scope="session")
def ensure_corpus(corpus_pdf: Path) -> Path:
    missing = [
        name
        for name in (
            "clean_single_column.pdf",
            "two_column.pdf",
            "parallel_columns.pdf",
            "variations.pdf",
            "figurine.pdf",
            "weird_typography.pdf",
            "diagram_heavy.pdf",
            "ambiguous.pdf",
            "with_toc_and_headers.pdf",
        )
        if not (corpus_pdf / name).exists()
    ]
    if missing:
        import generate

        generate.main()
    return corpus_pdf


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    s = Settings(data_dir=tmp_path / "data")
    s.ensure_dirs()
    init_db(s.db_path)
    return s


@pytest.fixture()
def settings_and_db(settings: Settings):
    init_db(settings.db_path)
    return settings