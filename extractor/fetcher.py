"""SEC HTTP client: User-Agent, rate limit, on-disk cache, allowlist."""

import asyncio
import hashlib
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx


CACHE_DIR = Path(os.environ.get("CACHE_DIR", "cache"))
SEC_DOMAINS = ("www.sec.gov", "data.sec.gov", "efts.sec.gov")


class TokenBucket:
    """Async token bucket. Default 10 req/s, burst 10 (SEC's published ceiling)."""

    def __init__(self, rate: float = 10.0, capacity: float = 10.0):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_refill = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            await asyncio.sleep(wait)


_bucket = TokenBucket()


def _user_agent() -> str:
    contact = os.environ.get("SEC_CONTACT_EMAIL", "").strip()
    if not contact:
        raise RuntimeError(
            "SEC_CONTACT_EMAIL env var is required. SEC mandates a contact in "
            "the User-Agent header. Set to a real email like 'you@example.com'."
        )
    return f"sec-10k-extractor/0.1 ({contact})"


def _allowed(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in SEC_DOMAINS


def _cache_path(url: str) -> Path:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{h}.bin"


async def fetch(url: str, *, force: bool = False) -> bytes:
    """GET `url`, cached by URL hash. Raises on non-2xx after retries."""
    if not _allowed(url):
        raise ValueError(
            f"URL not on SEC allowlist: {url!r} (host={urlparse(url).hostname})"
        )

    cache = _cache_path(url)
    if not force and cache.exists():
        return cache.read_bytes()

    await _bucket.acquire()

    headers = {
        "User-Agent": _user_agent(),
        "Accept-Encoding": "gzip, deflate",
        "Host": urlparse(url).hostname or "",
    }
    last_status = None
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for attempt in range(4):
            r = await client.get(url, headers=headers)
            last_status = r.status_code
            if r.status_code == 200:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(r.content)
                return r.content
            if r.status_code in (429, 500, 502, 503, 504):
                wait = min(30.0, 2 ** attempt)
                await asyncio.sleep(wait)
                await _bucket.acquire()
                continue
            r.raise_for_status()
    raise RuntimeError(
        f"Failed to fetch {url} after 4 retries (last status={last_status})"
    )
