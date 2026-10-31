"""Per-visitor rate limits for the endpoints that cost money (voice calls, LLM, web search).

All traffic reaches the API through the Cloudflare Tunnel (uvicorn listens on localhost
only), so Cloudflare's CF-Connecting-IP header is the visitor's real address and can't
be forged from outside. Counts live in memory: a restart resets them, which is fine for
abuse protection on one small server.
"""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

# name: (requests per IP, window seconds)
PER_IP = {
    "call": (6, 600),         # starting a Bolna voice call: minutes on the Bolna account
    "scan": (20, 600),        # photo scans, label reads, submissions: LLM + web search
    "compare": (20, 600),     # EU/US comparison: web search + LLM on a cache miss
    "live_lookup": (40, 600),  # typed questions on the live page: vision read + research
    "frame": (150, 60),       # live camera frames, about one a second
}
# Across everyone, so many addresses together can't run up the bill either.
GLOBAL = {"call": (60, 3600)}


class Limiter:
    def __init__(self) -> None:
        self.hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, name: str, ip: str) -> None:
        now = time.monotonic()
        self._take(name, ip, *PER_IP[name], now,
                   "Too many requests from your connection. Please wait a few minutes and try again.")
        if name in GLOBAL:
            self._take(name, "*", *GLOBAL[name], now,
                       "Fineprint is busy right now. Please try again in a little while.")

    def _take(self, name: str, who: str, limit: int, window: int, now: float, message: str) -> None:
        q = self.hits[(name, who)]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            retry = int(window - (now - q[0])) + 1
            raise HTTPException(429, message, headers={"Retry-After": str(retry)})
        q.append(now)
        if len(self.hits) > 50_000:  # drop idle visitors so memory stays bounded
            for key in [k for k, v in self.hits.items() if not v or now - v[-1] > 3600]:
                del self.hits[key]


def client_ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")


def limit(name: str):
    """FastAPI dependency: `dependencies=[Depends(limit("scan"))]`."""
    def dep(request: Request) -> None:
        request.app.state.limiter.check(name, client_ip(request))
    return dep
