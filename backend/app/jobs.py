"""Background jobs for photo scans.

A front-of-pack scan can take over a minute when the agent searches the web, longer than
Cloudflare allows a request to wait (100 s, fixed below the Enterprise plan). So the scan
runs in the background: POST returns a job ID at once, and the client polls for progress
and the result.

Each finished job's full trace is logged as one JSON line and handed to `on_finish` (which
stores it in Appwrite) for observability. Clients only need the coarse `stage`.

Jobs live in memory, which is fine with the single API worker we run.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from app.agent import AgentResult, Step
from app.llm import usage_totals
from app.models import Verdict

log = logging.getLogger("scans")

KEEP_FINISHED_FOR = 3600  # seconds
MAX_CONCURRENT = 3  # 2 vCPUs; scans mostly wait on the LLM, but embeddings use the CPU
WEB_TOOLS = {"web_search", "read_page"}

Stage = Literal["queued", "reading", "matching", "searching", "checking", "done", "failed"]


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

    @computed_field
    @property
    def stage(self) -> Stage:
        """Coarse progress for the UI, without exposing the agent's individual steps."""
        if self.status in ("queued", "done", "failed"):
            return self.status
        tools = {s.tool for s in self.steps}
        if "submit_product" in tools:
            return "checking"
        if tools & WEB_TOOLS:
            return "searching"
        if "read_pack" in tools:
            return "matching"
        return "reading"

    def log_record(self) -> dict:
        outcome = self.result.result if self.result else None
        usage = list(outcome.usage) if outcome and outcome.usage else []
        return {
            "job_id": self.id,
            "status": self.status,
            "outcome": outcome.status if outcome else None,
            "product_key": outcome.product.key if outcome and outcome.product else None,
            "duration_ms": round(((self.finished_at or time.time()) - self.created_at) * 1000),
            "error": self.error,
            "steps": [s.model_dump() for s in self.steps],
            "usage": usage[-50:],  # one record per LLM call; capped to bound log size
            "llm": usage_totals(usage),  # token + DeepSeek cache-hit + cost sums
        }


class JobStore:
    def __init__(self, on_finish: Callable[[dict], Awaitable[None]] | None = None,
                 max_concurrent: int = MAX_CONCURRENT) -> None:
        self.jobs: dict[str, Job] = {}
        self.on_finish = on_finish
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
        record = job.log_record()
        log.info(json.dumps(record, ensure_ascii=False))
        if self.on_finish:
            try:
                await self.on_finish(record)
            except Exception as e:  # observability must never break a scan
                log.warning("Could not store scan log %s: %s", job.id, e)

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def _prune(self) -> None:
        cutoff = time.time() - KEEP_FINISHED_FOR
        for job_id in [j.id for j in self.jobs.values() if j.finished_at and j.finished_at < cutoff]:
            del self.jobs[job_id]
