"""Import job manager (charter #32).

Jobs run on a worker thread so the API stays responsive; progress is polled or
streamed over SSE.  Nothing here reaches the network.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import repository
from .config import Settings, get_settings
from .db import connect, jdump, jload, now
from .models import STAGE_SEQUENCE, ImportJob, JobStage
from .pipeline.build import process_pdf
from .storage import (
    delete_book_dir,
    ensure_book_dirs,
    free_tmp,
    looks_like_pdf,
    new_id,
    sanitize_filename,
    sha256_file,
    store_upload,
    write_json,
)

log = logging.getLogger(__name__)


class JobManager:
    def __init__(self, settings: Settings | None = None, workers: int = 2) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_dirs()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="import")
        self._lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------

    def submit(self, upload_path: Path, filename: str, title: str | None = None) -> ImportJob:
        job = ImportJob(
            id=new_id("job"),
            filename=sanitize_filename(filename),
            status="queued",
            created_at=now(),
            updated_at=now(),
            stages=[
                JobStage(key=key, label=label) for key, label in STAGE_SEQUENCE
            ],
        )
        self._save(job)
        self._pool.submit(self._run, job.id, Path(upload_path), job.filename, title)
        return job

    def get(self, job_id: str) -> ImportJob | None:
        row = connect().execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def list(self, limit: int = 30) -> list[ImportJob]:
        rows = connect().execute(
            "SELECT * FROM import_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def issues(self, job_id: str, limit: int = 200) -> list[dict]:
        job = self.get(job_id)
        if job is None or not job.book_id:
            return []
        return repository.load_issues(job.book_id, limit=limit)

    # -- worker ----------------------------------------------------------

    def _run(self, job_id: str, upload_path: Path, filename: str, title: str | None) -> None:
        job = self.get(job_id)
        if job is None:
            return
        book_id = new_id("bk")
        job.book_id = book_id
        job.status = "running"
        self._save(job)
        tmp_dir = get_settings().tmp_dir / job_id
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            if not looks_like_pdf(upload_path):
                raise ValueError("The uploaded file is not a PDF")
            size = upload_path.stat().st_size
            if size > self.settings.max_upload_bytes:
                raise ValueError(
                    f"File is larger than the {self.settings.max_upload_bytes // (1024 * 1024)} MiB limit"
                )

            self._stage(job, "upload", "done", 1.0, f"{size // 1024} KiB")
            paths = ensure_book_dirs(book_id)
            original = store_upload(upload_path, book_id)

            self._stage(job, "hash", "running", 0.1)
            content_hash = sha256_file(original)
            self._stage(job, "hash", "done", 1.0, content_hash[:12])
            existing = repository.find_book_by_hash(content_hash)
            if existing:
                job.reuse_existing_book_id = existing
                job.book_id = existing
                job.status = "succeeded"
                job.progress = 1.0
                job.messages.append(f"Identical file already imported as {existing}")
                self._finish_stages(job)
                self._save(job)
                return

            def progress(key: str, value: float, detail: str = "") -> None:
                self._stage(job, key, "running", value, detail)

            output = process_pdf(
                original,
                book_id,
                title or _title_from_filename(filename),
                self.settings,
                progress=progress,
            )
            output.book.content_hash = content_hash
            output.book.source_filename = filename

            self._stage(job, "persist", "running", 0.2)
            repository.save_book(output, original)
            self._stage(job, "persist", "done", 1.0)

            self._finish_stages(job)
            job.progress = 1.0
            job.status = "succeeded"
            m = output.book.metrics
            job.messages.append(
                f"{m.games} games, {m.chapters} chapters, {m.moves} moves, "
                f"{m.variations} variations, {m.diagrams} diagrams"
            )
            if m.issues:
                job.messages.append(f"{m.issues} extraction issues — see the issues report")
            self._save(job)
        except Exception as exc:  # noqa: BLE001 - the job must survive anything
            job.status = "failed"
            job.error = f"{exc.__class__.__name__}: {exc}"
            job.logs.extend(traceback.format_exc().splitlines()[-12:])
            log.exception("import job %s failed", job_id)
            self._save(job)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            try:
                upload_path.unlink(missing_ok=True)
            except OSError:
                pass
            free_tmp()

    # -- persistence -----------------------------------------------------

    @staticmethod
    def _finish_stages(job: ImportJob) -> None:
        """Close the stage list: what ran is done, what never started skipped."""
        for stage in job.stages:
            if stage.status in {"running", "done", "skipped"}:
                continue
            stage.status = "skipped"
        for stage in job.stages:
            if stage.status == "running":
                stage.status = "done"
                stage.progress = 1.0
                stage.ended_at = stage.ended_at or time.time()

    def _stage(self, job: ImportJob, key: str, status: str, value: float, detail: str = "") -> None:
        stage = next((s for s in job.stages if s.key == key), None)
        if stage is None:
            return
        if status == "running":
            stage.status = "running"
            stage.progress = max(stage.progress, min(1.0, value))
            if detail:
                stage.detail = detail[:160]
        elif status == "done":
            stage.status = "done"
            stage.progress = 1.0
            stage.ended_at = now()
            if detail:
                stage.detail = detail[:160]
        elif status == "skipped":
            stage.status = "skipped"
            if detail:
                stage.detail = detail[:160]

        done = sum(1 for s in job.stages if s.status in ("done", "skipped"))
        job.progress = round(done / max(1, len(job.stages)) * 0.98, 3)
        job.updated_at = now()
        self._save(job)

    def _save(self, job: ImportJob) -> None:
        with self._lock, connect() as conn:
            conn.execute(
                """INSERT INTO import_jobs(id, book_id, filename, status, progress, stages,
                        messages, logs, error, reuse_book_id, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET book_id=excluded.book_id,
                     status=excluded.status, progress=excluded.progress, stages=excluded.stages,
                     messages=excluded.messages, logs=excluded.logs, error=excluded.error,
                     reuse_book_id=excluded.reuse_book_id, updated_at=excluded.updated_at""",
                (
                    job.id,
                    job.book_id,
                    job.filename,
                    job.status,
                    job.progress,
                    jdump([s.model_dump() for s in job.stages]),
                    jdump(job.messages),
                    jdump(job.logs),
                    job.error,
                    job.reuse_existing_book_id,
                    job.created_at,
                    job.updated_at,
                ),
            )
            conn.commit()

    @staticmethod
    def _row_to_job(row) -> ImportJob:
        d = dict(row)
        return ImportJob(
            id=d["id"],
            book_id=d["book_id"],
            filename=d["filename"],
            status=d["status"],
            progress=d["progress"],
            stages=[JobStage(**s) for s in jload(d["stages"], [])],
            messages=jload(d["messages"], []),
            logs=jload(d["logs"], []),
            error=d["error"],
            reuse_existing_book_id=d["reuse_book_id"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )


def _title_from_filename(filename: str) -> str:
    stem = Path(sanitize_filename(filename)).stem
    return stem.replace("_", " ").replace("-", " ").strip().title() or "Untitled book"


_manager: JobManager | None = None


def get_manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager()
    return _manager