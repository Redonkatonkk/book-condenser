from __future__ import annotations

import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fastapi import UploadFile

from app import config
from app.book_parser import parse_book
from app.credentials import CredentialStore, MiniMaxCredential
from app.epub_writer import write_condensed_epub
from app.minimax_client import MiniMaxAuthError, MiniMaxClient
from app.schemas import Chapter, ChapterStatus, IntegrityReport, JobStatus, ParsedBook
from app.text_utils import count_units, safe_filename_part


@dataclass
class ChapterProgress:
    id: str
    title: str
    original_count: int
    condensed_count: int = 0
    progress: int = 0
    status: ChapterStatus = ChapterStatus.pending
    error: str = ""
    condensed_text: str = ""
    started_at: float | None = None
    completed_at: float | None = None


@dataclass
class Job:
    id: str
    filename: str
    model: str
    region: str = config.DEFAULT_REGION
    status: JobStatus = JobStatus.queued
    title: str = ""
    author: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    error: str = ""
    integrity: IntegrityReport | None = None
    chapters: list[ChapterProgress] = field(default_factory=list)
    output_path: str = ""
    upload_path: str = ""
    active_batch_total: int = 0
    active_batch_done: int = 0
    active_batch_id: str = ""
    active_batch_chapter_ids: list[str] = field(default_factory=list)
    stop_requested: bool = False
    active_elapsed_seconds: float = 0.0
    active_batch_started_at: float | None = None


class JobManager:
    def __init__(
        self,
        storage_dir: Path = config.STORAGE_DIR,
        ai_client: MiniMaxClient | None = None,
        max_workers: int = config.MAX_WORKERS,
    ) -> None:
        self.storage_dir = storage_dir
        self.ai_client = ai_client or MiniMaxClient()
        self.credential_store = CredentialStore(storage_dir)
        self.max_workers = max_workers
        self.jobs: dict[str, Job] = {}
        self.job_credentials: dict[str, tuple[MiniMaxCredential, bool]] = {}
        self.job_source_chapters: dict[str, list[Chapter]] = {}
        self.job_images: dict[str, list[dict]] = {}
        self.active_condense_jobs: set[str] = set()
        self.lock = threading.RLock()
        self.runner = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job-runner")

    def create_job(
        self,
        upload: UploadFile,
        model: str,
        api_key: str = "",
        region: str = config.DEFAULT_REGION,
    ) -> Job:
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in config.ALLOWED_EXTENSIONS:
            raise ValueError("仅支持 EPUB、PDF、TXT 文件。")
        if model not in config.SUPPORTED_MODELS:
            raise ValueError("不支持所选 MiniMax 模型。")
        credential, from_user = self._resolve_credential(api_key, region)
        if not config.MOCK_AI and credential is None:
            raise ValueError("后台未配置 MiniMax API Key，请填写 API Key 后再开始。")

        job_id = uuid.uuid4().hex
        job_dir = self.storage_dir / "jobs" / job_id
        upload_dir = job_dir / "upload"
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_name = safe_filename_part(Path(upload.filename or "book").stem, "book") + suffix
        upload_path = upload_dir / safe_name
        with upload_path.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)
        if upload_path.stat().st_size > config.MAX_UPLOAD_BYTES:
            upload_path.unlink(missing_ok=True)
            raise ValueError("上传文件过大。")

        job = Job(
            id=job_id,
            filename=upload.filename or safe_name,
            model=model,
            region=credential.region if credential else region,
            upload_path=str(upload_path),
        )
        with self.lock:
            self.jobs[job_id] = job
            if credential:
                self.job_credentials[job_id] = (credential, from_user)
        self.runner.submit(self._analyze_job, job_id)
        return job

    def get_job(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def snapshot(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            completed = sum(1 for chapter in job.chapters if chapter.status == ChapterStatus.done)
            running = sum(1 for chapter in job.chapters if chapter.status == ChapterStatus.running)
            failed = sum(1 for chapter in job.chapters if chapter.status == ChapterStatus.failed)
            pending = sum(1 for chapter in job.chapters if chapter.status == ChapterStatus.pending)
            total = len(job.chapters)
            progress = 0
            if job.status == JobStatus.completed:
                progress = 100
            elif total:
                progress = int((completed + running * 0.5) / total * 100)
            eta = self._estimate_eta(job, completed, total)
            data = asdict(job)
            data["status"] = job.status.value
            data["progress"] = progress
            data["completed_count"] = completed
            data["failed_count"] = failed
            data["pending_count"] = pending
            data["running_count"] = running
            data["eta_seconds"] = eta
            data["elapsed_seconds"] = self._elapsed_seconds(job)
            data["chapters"] = [
                {
                    "id": chapter.id,
                    "title": chapter.title,
                    "original_count": chapter.original_count,
                    "condensed_count": chapter.condensed_count,
                    "progress": chapter.progress,
                    "status": chapter.status.value,
                    "error": chapter.error,
                }
                for chapter in job.chapters
            ]
            data["integrity"] = asdict(job.integrity) if job.integrity else None
            data["download_ready"] = completed > 0
            return data

    def get_chapter_content(self, job_id: str, chapter_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            for chapter in job.chapters:
                if chapter.id == chapter_id:
                    return {
                        "id": chapter.id,
                        "title": chapter.title,
                        "status": chapter.status.value,
                        "original_count": chapter.original_count,
                        "condensed_count": chapter.condensed_count,
                        "content": chapter.condensed_text,
                        "error": chapter.error,
                    }
        return None

    def condense(self, job_id: str, mode: str, chapter_ids: list[str] | None = None) -> list[str]:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError("任务不存在。")
            if job.status in {JobStatus.queued, JobStatus.analyzing}:
                raise ValueError("书籍仍在分析中，请稍后。")
            if job.status == JobStatus.condensing or job_id in self.active_condense_jobs:
                raise ValueError("当前已有浓缩批次在运行。")
            if job.status == JobStatus.failed:
                raise ValueError(job.error or "任务失败，无法继续。")
            selected_ids = self._select_chapter_ids(job, mode, chapter_ids or [])
            if not selected_ids:
                raise ValueError("没有可浓缩的章节。")
            for chapter in job.chapters:
                if chapter.id in selected_ids:
                    chapter.status = ChapterStatus.pending
                    chapter.progress = 0
                    chapter.error = ""
            job.error = ""
            job.status = JobStatus.condensing
            job.active_batch_total = len(selected_ids)
            job.active_batch_done = 0
            job.active_batch_id = uuid.uuid4().hex
            job.active_batch_chapter_ids = selected_ids
            job.stop_requested = False
            if job.started_at is None:
                job.started_at = time.time()
            job.active_batch_started_at = time.time()
            self.active_condense_jobs.add(job_id)
            batch_id = job.active_batch_id
        self.runner.submit(self._run_condense_batch, job_id, selected_ids, batch_id)
        return selected_ids

    def stop_condense(self, job_id: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError("任务不存在。")
            if job.status != JobStatus.condensing and job_id not in self.active_condense_jobs:
                return False
            active_ids = set(job.active_batch_chapter_ids)
            job.stop_requested = True
            self._accumulate_active_elapsed_locked(job)
            job.status = JobStatus.ready
            job.error = "已停止当前浓缩批次，可重新选择章节操作。"
            job.active_batch_total = 0
            job.active_batch_done = 0
            job.active_batch_id = ""
            job.active_batch_chapter_ids = []
            job.active_batch_started_at = None
            for chapter in job.chapters:
                if chapter.id in active_ids and chapter.status in {
                    ChapterStatus.pending,
                    ChapterStatus.running,
                }:
                    chapter.status = ChapterStatus.pending
                    chapter.progress = 0
                    chapter.error = ""
                    chapter.started_at = None
            self.active_condense_jobs.discard(job_id)
            return True

    def export_epub(self, job_id: str, chapter_ids: list[str] | None = None) -> Path:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError("任务不存在。")
            requested = set(chapter_ids or [])
            chapters = [
                {
                    "title": chapter.title,
                    "content": chapter.condensed_text,
                }
                for chapter in job.chapters
                if chapter.status == ChapterStatus.done
                and chapter.condensed_text
                and (not requested or chapter.id in requested)
            ]
            title = job.title or Path(job.filename).stem
            author = job.author
            images = self.job_images.get(job_id, [])
        if not chapters:
            raise ValueError("没有可导出的已完成章节。")
        export_id = uuid.uuid4().hex
        output_path = (
            self.storage_dir
            / "jobs"
            / job_id
            / "exports"
            / f"{safe_filename_part(title)}_{export_id[:8]}.epub"
        )
        return write_condensed_epub(
            output_path,
            identifier=f"{job_id}-{export_id}",
            title=title,
            author=author,
            chapters=chapters,
            images=images,
        )

    def _analyze_job(self, job_id: str) -> None:
        try:
            self._set_job_status(job_id, JobStatus.analyzing)
            job = self.get_job(job_id)
            if not job:
                return
            with self.lock:
                credential_pair = self.job_credentials.get(job_id)
            credential = credential_pair[0] if credential_pair else None
            self.ai_client.validate_api_key(
                credential.api_key if credential else None,
                api_url=credential.resolved_api_url if credential else None,
            )
            if credential_pair and credential_pair[1]:
                self.credential_store.save(
                    credential.api_key,
                    credential.region,
                    credential.api_url,
                )
            parsed = parse_book(Path(job.upload_path), job.filename)
            self._store_parsed_book(job_id, parsed)
            if not parsed.chapters:
                raise ValueError("没有可浓缩的章节。")
            with self.lock:
                job = self.jobs[job_id]
                job.status = JobStatus.ready
        except MiniMaxAuthError as exc:
            self.credential_store.clear()
            with self.lock:
                job = self.jobs.get(job_id)
                if job:
                    job.status = JobStatus.failed
                    job.error = str(exc)
                    job.completed_at = time.time()
        except Exception as exc:
            with self.lock:
                job = self.jobs.get(job_id)
                if job:
                    job.status = JobStatus.failed
                    job.error = str(exc)
                    job.completed_at = time.time()
        finally:
            pass

    def _store_parsed_book(self, job_id: str, parsed: ParsedBook) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job.title = parsed.title
            job.author = parsed.author
            job.integrity = parsed.integrity
            job.chapters = [
                ChapterProgress(
                    id=chapter.id,
                    title=chapter.title,
                    original_count=chapter.original_count,
                )
                for chapter in parsed.chapters
            ]
            self.job_source_chapters[job_id] = parsed.chapters
            self.job_images[job_id] = [asdict(image) for image in parsed.images]

    def _run_condense_batch(self, job_id: str, chapter_ids: list[str], batch_id: str) -> None:
        try:
            with self.lock:
                source_by_id = {
                    chapter.id: chapter for chapter in self.job_source_chapters.get(job_id, [])
                }
                chapters = [source_by_id[chapter_id] for chapter_id in chapter_ids if chapter_id in source_by_id]
            self._condense_all_chapters(job_id, chapters, batch_id)
        finally:
            with self.lock:
                job = self.jobs.get(job_id)
                if not job or job.active_batch_id != batch_id:
                    return
                if job:
                    self._accumulate_active_elapsed_locked(job)
                    completed = sum(1 for chapter in job.chapters if chapter.status == ChapterStatus.done)
                    failed = sum(1 for chapter in job.chapters if chapter.status == ChapterStatus.failed)
                    running = any(chapter.status == ChapterStatus.running for chapter in job.chapters)
                    if running:
                        job.status = JobStatus.condensing
                    elif job.chapters and completed == len(job.chapters):
                        job.status = JobStatus.completed
                        job.completed_at = time.time()
                    else:
                        job.status = JobStatus.ready
                        if failed:
                            job.error = f"有 {failed} 个章节浓缩失败，可重试失败章节。"
                    job.active_batch_total = 0
                    job.active_batch_done = 0
                    job.active_batch_id = ""
                    job.active_batch_chapter_ids = []
                    job.stop_requested = False
                    job.active_batch_started_at = None
                self.active_condense_jobs.discard(job_id)

    def _condense_all_chapters(self, job_id: str, chapters: list[Chapter], batch_id: str) -> None:
        workers = min(self.max_workers, max(1, len(chapters)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chapter") as executor:
            future_map = {
                executor.submit(self._condense_one, job_id, chapter, batch_id): chapter.id
                for chapter in chapters
            }
            errors: list[str] = []
            for future in as_completed(future_map):
                chapter_id = future_map[future]
                try:
                    future.result()
                except Exception as exc:
                    errors.append(f"{chapter_id}: {exc}")

    def _condense_one(self, job_id: str, chapter: Chapter, batch_id: str) -> None:
        if not self._is_batch_active(job_id, batch_id):
            return
        self._update_chapter(
            job_id,
            chapter.id,
            status=ChapterStatus.running,
            progress=0,
            started_at=time.time(),
        )
        try:
            with self.lock:
                job = self.jobs[job_id]
                model = job.model
                credential_pair = self.job_credentials.get(job_id)
                credential = credential_pair[0] if credential_pair else None
            minimum_count = max(1, int(chapter.original_count * 0.2 + 0.999))
            condensed = self.ai_client.condense_chapter(
                chapter.title,
                chapter.text,
                model,
                api_key=credential.api_key if credential else None,
                api_url=credential.resolved_api_url if credential else None,
                original_count=chapter.original_count,
                minimum_count=minimum_count,
            )
            if not self._is_batch_active(job_id, batch_id):
                return
            self._update_chapter(
                job_id,
                chapter.id,
                status=ChapterStatus.done,
                progress=0,
                condensed_text=condensed,
                condensed_count=count_units(condensed),
                completed_at=time.time(),
            )
            self._increment_batch_done(job_id)
        except Exception as exc:
            if not self._is_batch_active(job_id, batch_id):
                return
            self._update_chapter(
                job_id,
                chapter.id,
                status=ChapterStatus.failed,
                progress=0,
                error=str(exc),
                completed_at=time.time(),
            )
            self._increment_batch_done(job_id)
            raise

    def _build_epub(self, job_id: str) -> Path:
        with self.lock:
            job = self.jobs[job_id]
            chapters = [
                {
                    "title": chapter.title,
                    "content": chapter.condensed_text,
                }
                for chapter in job.chapters
                if chapter.status == ChapterStatus.done
            ]
            title = job.title or Path(job.filename).stem
            author = job.author
            images = self.job_images.get(job_id, [])
        if not chapters:
            raise RuntimeError("没有已完成的章节可导出。")
        output_path = self.storage_dir / "jobs" / job_id / "output" / f"{safe_filename_part(title)}_condensed.epub"
        return write_condensed_epub(
            output_path,
            identifier=job_id,
            title=title,
            author=author,
            chapters=chapters,
            images=images,
        )

    def _set_job_status(self, job_id: str, status: JobStatus, started: bool = False) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job.status = status
            if started and job.started_at is None:
                job.started_at = time.time()

    def _update_chapter(self, job_id: str, chapter_id: str, **changes) -> None:
        with self.lock:
            job = self.jobs[job_id]
            for chapter in job.chapters:
                if chapter.id == chapter_id:
                    for key, value in changes.items():
                        setattr(chapter, key, value)
                    return
            raise KeyError(chapter_id)

    def _increment_batch_done(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job:
                job.active_batch_done += 1

    def _is_batch_active(self, job_id: str, batch_id: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            return bool(
                job
                and job.status == JobStatus.condensing
                and job.active_batch_id == batch_id
                and not job.stop_requested
            )

    def _estimate_eta(self, job: Job, completed: int, total: int) -> int | None:
        if job.status not in {JobStatus.condensing, JobStatus.building}:
            return None
        if total == 0:
            return None
        samples = [
            chapter
            for chapter in job.chapters
            if chapter.status == ChapterStatus.done
            and chapter.started_at is not None
            and chapter.completed_at is not None
            and chapter.completed_at > chapter.started_at
            and chapter.original_count > 0
        ]
        if not samples:
            return None
        sample_seconds = sum(chapter.completed_at - chapter.started_at for chapter in samples)
        sample_units = sum(chapter.original_count for chapter in samples)
        if sample_seconds <= 0 or sample_units <= 0:
            return None
        active_ids = set(job.active_batch_chapter_ids)
        remaining = [
            chapter
            for chapter in job.chapters
            if chapter.id in active_ids
            and chapter.status in {ChapterStatus.pending, ChapterStatus.running}
        ]
        if not remaining:
            return 0
        remaining_units = sum(max(1, chapter.original_count) for chapter in remaining)
        workers = max(1, min(self.max_workers, len(active_ids) or len(remaining)))
        return int(max(0, (sample_seconds / sample_units) * remaining_units / workers))

    def _elapsed_seconds(self, job: Job) -> int | None:
        if not job.started_at and job.active_elapsed_seconds <= 0:
            return None
        elapsed = job.active_elapsed_seconds
        if job.status == JobStatus.condensing and job.active_batch_started_at:
            elapsed += time.time() - job.active_batch_started_at
        return int(max(0, elapsed))

    def _accumulate_active_elapsed_locked(self, job: Job) -> None:
        if job.active_batch_started_at is None:
            return
        job.active_elapsed_seconds += max(0.0, time.time() - job.active_batch_started_at)
        job.active_batch_started_at = None

    def _resolve_credential(
        self, api_key: str, region: str
    ) -> tuple[MiniMaxCredential | None, bool]:
        normalized_region = region.strip().lower()
        if normalized_region not in config.REGION_ENDPOINTS:
            normalized_region = config.DEFAULT_REGION
        task_api_key = api_key.strip()
        if task_api_key:
            return MiniMaxCredential(api_key=task_api_key, region=normalized_region), True
        if self.ai_client.api_key:
            return (
                MiniMaxCredential(
                    api_key=self.ai_client.api_key,
                    region=config.DEFAULT_REGION,
                    api_url=self.ai_client.api_url,
                ),
                False,
            )
        stored = self.credential_store.load()
        if stored:
            return stored, False
        return None, False

    def _select_chapter_ids(self, job: Job, mode: str, chapter_ids: list[str]) -> list[str]:
        chapter_by_id = {chapter.id: chapter for chapter in job.chapters}
        if mode == "selected":
            return [
                chapter_id
                for chapter_id in chapter_ids
                if chapter_id in chapter_by_id
                and chapter_by_id[chapter_id].status in {ChapterStatus.pending, ChapterStatus.failed}
            ]
        if mode == "failed":
            return [
                chapter.id for chapter in job.chapters if chapter.status == ChapterStatus.failed
            ]
        candidates = [
            chapter.id
            for chapter in job.chapters
            if chapter.status in {ChapterStatus.pending, ChapterStatus.failed}
        ]
        if mode == "one":
            return candidates[:1]
        if mode == "ten":
            return candidates[:10]
        if mode == "all":
            return candidates
        raise ValueError("未知浓缩模式。")
