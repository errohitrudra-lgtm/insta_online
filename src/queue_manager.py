"""
Task queue manager.

Each job flows through:
    PENDING → DOWNLOADING → DOWNLOADED → UPLOADING → UPLOADED  (or FAILED)
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional

from .logger import get_logger

log = get_logger("queue")


class JobStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    UPLOAD_QUEUED = "upload_queued"
    FAILED = "failed"


@dataclass
class Job:
    """A download + upload job for a single reel."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    target_username: str = ""
    reel_shortcode: str = ""
    video_url: str = ""
    caption: str = ""
    view_count: int = 0
    like_count: int = 0
    permalink: str = ""
    status: JobStatus = JobStatus.PENDING
    local_path: str = ""
    upload_result: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    attempts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


JobWorker = Callable[[Job], Coroutine[Any, Any, None]]


class MemoryQueue:
    """asyncio.Queue-backed task queue with worker pool."""

    def __init__(self, max_workers: int = 2) -> None:
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._jobs: Dict[str, Job] = {}
        self._max_workers = max_workers
        self._workers: List[asyncio.Task] = []
        self._worker_fn: Optional[JobWorker] = None
        self._running = False

    async def start(self, worker_fn: JobWorker) -> None:
        self._worker_fn = worker_fn
        self._running = True
        for i in range(self._max_workers):
            task = asyncio.create_task(
                self._worker_loop(i), name=f"queue-worker-{i}"
            )
            self._workers.append(task)
        log.info("Queue started with %d worker(s)", self._max_workers)

    async def stop(self) -> None:
        self._running = False
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        log.info("Queue stopped")

    async def enqueue(self, job: Job) -> str:
        self._jobs[job.id] = job
        await self._queue.put(job)
        log.info("Enqueued job %s (reel %s)", job.id, job.reel_shortcode)
        return job.id

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def get_all_jobs(self) -> List[Job]:
        return list(self._jobs.values())

    @property
    def jobs(self) -> Dict[str, Job]:
        """Access all jobs (read-only view)."""
        return self._jobs

    def get_downloaded_not_uploaded(self) -> List[Job]:
        """Return jobs that are downloaded but not yet uploaded."""
        return [
            j for j in self._jobs.values()
            if j.status in (JobStatus.DOWNLOADED, JobStatus.UPLOAD_QUEUED)
            and j.local_path
        ]

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    async def _worker_loop(self, worker_id: int) -> None:
        while self._running:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return

            log.info("Worker %d processing job %s", worker_id, job.id)
            try:
                if self._worker_fn:
                    await self._worker_fn(job)
            except Exception as exc:
                job.status = JobStatus.FAILED
                job.error = str(exc)
                job.updated_at = time.time()
                log.exception("Job %s failed: %s", job.id, exc)
            finally:
                self._queue.task_done()


def create_queue(
    backend: str = "memory",
    redis_url: str = "",
    max_workers: int = 2,
) -> MemoryQueue:
    log.info("Using in-memory queue with %d workers", max_workers)
    return MemoryQueue(max_workers)
