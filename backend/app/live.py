"""Latest camera frame per live-voice session.

The browser posts a frame about once a second; when Bolna calls the live-lookup
tool we use the freshest frame for that session. Frames stay in memory only and
expire quickly - nothing is written to Appwrite Storage until a product is
identified (the identify job saves the pack photo then).
"""

import re
import time
from dataclasses import dataclass

SESSION_RE = re.compile(r"^[A-Za-z0-9-_]{8,64}$")

FRAME_TTL = 15.0  # drop sessions idle this long
STALE_AFTER = 5.0  # frame older than this: ask the user to keep pointing
MAX_SESSIONS = 500


@dataclass
class LiveFrame:
    data: bytes
    media_type: str
    received_at: float


@dataclass
class LiveResult:
    """Last tool answer for a session, so the web page can show cards for
    answers spoken on a Bolna voice call it can't hear."""

    answer: str
    reason: str
    product_key: str | None
    job_id: str | None
    poll_url: str | None
    at: float
    snap_at: float | None = None  # which snap this answers


@dataclass
class LiveSnap:
    """The frame a lookup is working on, so the page can freeze it on screen
    and show the caller exactly what is being looked at."""

    data: bytes
    media_type: str
    at: float


class LiveStore:
    def __init__(self) -> None:
        self.frames: dict[str, LiveFrame] = {}
        self.results: dict[str, LiveResult] = {}
        self.snaps: dict[str, LiveSnap] = {}
        self.looking: set[str] = set()
        # session -> (job id, snap_at) of its background lookup, and when it started
        self.research_jobs: dict[str, tuple[str, float | None, float]] = {}

    def put(self, session_id: str, data: bytes, media_type: str) -> None:
        self._prune()
        if len(self.frames) >= MAX_SESSIONS and session_id not in self.frames:
            oldest = min(self.frames, key=lambda k: self.frames[k].received_at)
            del self.frames[oldest]
            self.results.pop(oldest, None)
            self.snaps.pop(oldest, None)
            self.looking.discard(oldest)
            self.research_jobs.pop(oldest, None)
        self.frames[session_id] = LiveFrame(data, media_type, time.time())

    def get(self, session_id: str) -> LiveFrame | None:
        frame = self.frames.get(session_id)
        if frame is None:
            return None
        if time.time() - frame.received_at > FRAME_TTL:
            del self.frames[session_id]
            return None
        return frame

    def start_lookup(self, session_id: str, frame: LiveFrame) -> LiveSnap:
        snap = LiveSnap(frame.data, frame.media_type, time.time())
        self.snaps[session_id] = snap
        self.looking.add(session_id)
        return snap

    def note_result(self, session_id: str, result: LiveResult) -> None:
        self.results[session_id] = result
        self.looking.discard(session_id)

    def set_research(self, session_id: str, job_id: str, snap_at: float | None) -> None:
        self.research_jobs[session_id] = (job_id, snap_at, time.time())

    def research(self, session_id: str, ttl: float) -> tuple[str, float | None] | None:
        """This session's background lookup (job id, snap_at), if recent."""
        entry = self.research_jobs.get(session_id)
        if entry is None or time.time() - entry[2] > ttl:
            self.research_jobs.pop(session_id, None)
            return None
        return entry[0], entry[1]

    def clear_research(self, session_id: str) -> None:
        self.research_jobs.pop(session_id, None)

    def snap(self, session_id: str) -> LiveSnap | None:
        return self.snaps.get(session_id)

    def last_result(self, session_id: str) -> LiveResult | None:
        return self.results.get(session_id)

    def _prune(self) -> None:
        cutoff = time.time() - FRAME_TTL
        for key in [k for k, f in self.frames.items() if f.received_at < cutoff]:
            del self.frames[key]
            self.results.pop(key, None)
            self.snaps.pop(key, None)
            self.looking.discard(key)
            self.research_jobs.pop(key, None)
