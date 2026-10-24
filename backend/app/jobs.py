"""Background jobs for photo scans.

A front-of-pack scan can take over a minute when the agent searches the web, longer than
Cloudflare allows a request to wait (100 s, fixed below the Enterprise plan). So the scan
runs in the background: POST returns a job ID at once, and the client polls for progress
(each agent step appears as it finishes) and the result.

Jobs live in memory, which is fine with the single API worker we run.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel, Field

from app.agent import AgentResult, Step
from app.models import Verdict

log = logging.getLogger(__name__)

KEEP_FINISHED_FOR = 3600  # seconds
MAX_CONCURRENT = 3  # 2 vCPUs; scans mostly wait on the LLM, but embeddings use the CPU


class IdentifyResponse(BaseModel):
    result: AgentResult
    verdict: Verdict | None = None


class Job(BaseModel):
    id: str
    status: Literal["queued", "running", "done", "failed"] = "queued"
    created_at: float = Field(default_factory=time.time)
    finished_at: float | None = None
    steps: list[Step] = Field(default_factory=list)
    result: IdentifyResponse | None = None
    error: str | None = None


class JobStore:
    def __init__(self, max_concurrent: int = MAX_CONCURRENT) -> None:
        self.jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task] = set()  # keep references so tasks aren't garbage-collected
        self._slots = asyncio.Semaphore(max_concurrent)

    def start(self, work: Callable[[list[Step]], Awaitable[IdentifyResponse]]) -> Job:
        """Run `work(steps)` in the background. It appends progress to `steps` as it goes."""
        self._prune()
        job = Job(id=uuid.uuid4().hex)
        self.jobs[job.id] = job
        task = asyncio.create_task(self._run(job, work))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run(self, job: Job, work) -> None:
        async with self._slots:
            job.status = "running"
            try:
                job.result = await work(job.steps)
                job.status = "done"
            except Exception as e:
                log.exception("Job %s failed", job.id)
                job.error = str(e)
                job.status = "failed"
            finally:
                job.finished_at = time.time()

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def _prune(self) -> None:
        cutoff = time.time() - KEEP_FINISHED_FOR
        for job_id in [j.id for j in self.jobs.values() if j.finished_at and j.finished_at < cutoff]:
            del self.jobs[job_id]
