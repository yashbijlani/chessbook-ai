"""Application configuration.

Charter rule #3: ZERO API keys, ZERO remote services.
This module deliberately reads no credentials and refuses to start if any
LLM-provider environment variable is *required*.  (If such variables happen to
exist in the developer's shell they are simply ignored.)
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

# Every env var that would indicate an accidental cloud dependency.  We record
# them purely so the import pipeline can report "cloud credentials present but
# unused" in debug mode -- functionality never reads them.
CLOUD_CREDENTIAL_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "TOGETHER_API_KEY",
    "REPLICATE_API_TOKEN",
    "HF_TOKEN",
    "HUGGINGFACEHUB_API_TOKEN",
)


class Settings(BaseModel):
    """Filesystem layout + hard limits.  All state is local."""

    data_dir: Path = Field(default_factory=lambda: _default_data_dir())

    # Uploads / security (#43)
    max_upload_bytes: int = 512 * 1024 * 1024
    max_pdf_pages: int = 5000

    # Pipeline
    pdf_dpi: int = 200  # only used for rasterising pages for OCR/diagrams
    ocr_enabled: bool = True
    ocr_language: str = "eng"
    ocr_min_dpi: int = 200

    # Optional engine (#38).  Never mandatory.
    stockfish_path: str | None = None
    engine_threads: int = 1
    engine_hash_ms: int = 400

    @property
    def books_dir(self) -> Path:
        return self.data_dir / "books"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "chessbook.sqlite3"

    def book_dir(self, book_id: str) -> Path:
        return self.books_dir / book_id

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.books_dir, self.jobs_dir, self.tmp_dir):
            p.mkdir(parents=True, exist_ok=True)

    def present_cloud_credentials(self) -> list[str]:
        """Names (never values) of cloud creds present in the environment."""
        return [k for k in CLOUD_CREDENTIAL_VARS if os.environ.get(k)]


def _default_data_dir() -> Path:
    env = os.environ.get("CHESSBOOK_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    # backend/chessbook/config.py -> repo root
    repo_root = Path(__file__).resolve().parents[2]
    return (repo_root / "data").resolve()


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings


def set_settings(settings: Settings) -> None:
    global _settings
    settings.ensure_dirs()
    _settings = settings