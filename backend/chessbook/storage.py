"""Filesystem storage + security helpers (#43).

Originals live in ``data/books/<book_id>/original.pdf``; everything derived
lives beside it in clearly separated subdirectories so raw extraction is never
destroyed (#41).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import unicodedata
import uuid
from pathlib import Path

from .config import get_settings

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

ALLOWED_PDF_MAGIC = (b"%PDF-",)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def sanitize_filename(name: str | None) -> str:
    """Strip directories, control chars, and traversal sequences."""
    if not name:
        return "upload.pdf"
    name = unicodedata.normalize("NFC", name)
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable())
    name = _UNSAFE.sub("_", name).strip("._-")
    if not name:
        name = "upload.pdf"
    if len(name) > 120:
        stem, dot, ext = name.rpartition(".")
        ext = f".{dot}{ext}" if dot else ""
        name = stem[: 120 - len(ext)] + ext
    return name


def validate_id(value: str) -> str:
    if not _SAFE_ID.match(value or ""):
        raise ValueError(f"unsafe identifier: {value!r}")
    return value


def safe_join(base: Path, *parts: str) -> Path:
    """Join and guarantee the result stays inside ``base``."""
    base = base.resolve()
    target = base
    for part in parts:
        if part in ("", ".", ".."):
            continue
        target = (target / part).resolve()
    if base != target and base not in target.parents:
        raise ValueError("path traversal attempt blocked")
    return target


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def looks_like_pdf(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(1024)
    except OSError:
        return False
    return any(magic in head for magic in ALLOWED_PDF_MAGIC)


def book_paths(book_id: str) -> dict[str, Path]:
    validate_id(book_id)
    root = get_settings().book_dir(book_id)
    return {
        "root": root,
        "original": root / "original.pdf",
        "metadata": root / "metadata.json",
        "extracted": root / "extracted",
        "pages_text": root / "extracted" / "pages.json",
        "layout": root / "extracted" / "layout.json",
        "games": root / "extracted" / "games.json",
        "diagrams": root / "diagrams",
        "debug": root / "debug",
        "corrections": root / "corrections.json",
    }


def ensure_book_dirs(book_id: str) -> dict[str, Path]:
    paths = book_paths(book_id)
    for key in ("root", "extracted", "diagrams", "debug"):
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


def write_json(path: Path, data: object, indent: int | None = 2) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=indent, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    import json

    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def store_upload(src: Path, book_id: str) -> Path:
    paths = ensure_book_dirs(book_id)
    dst = paths["original"]
    shutil.move(str(src), dst)
    return dst


def delete_book_dir(book_id: str) -> None:
    root = get_settings().book_dir(validate_id(book_id))
    if root.exists():
        shutil.rmtree(root)


def free_tmp(max_age_seconds: float = 3600 * 6) -> int:
    """Remove stale temp artifacts (#43 'clean temporary files')."""
    import time

    tmp = get_settings().tmp_dir
    now = time.time()
    removed = 0
    if not tmp.exists():
        return 0
    for item in tmp.iterdir():
        try:
            if now - item.stat().st_mtime > max_age_seconds:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def relative_to_data(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(get_settings().data_dir))
    except ValueError:
        return str(path)