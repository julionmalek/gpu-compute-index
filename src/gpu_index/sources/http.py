"""Polite HTTP helpers shared by every source: a clear User-Agent, timeouts, and
backoff on 429/5xx that honours Retry-After."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable, Iterator

USER_AGENT = "gpu-compute-index/0.1 (+https://github.com/julionmalek/gpu-compute-index)"
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 5


def open_url(url: str, *, sleep: Callable[[float], None] = time.sleep,
             opener: Callable = urllib.request.urlopen, timeout: float = 60):
    """Open a URL, retrying when the server says it's busy. Returns the response."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return opener(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            wait = float(retry_after) if retry_after and retry_after.isdigit() else 5 * 2 ** (attempt - 1)
            sleep(min(wait, 120))
    raise AssertionError("unreachable")


def get_json(url: str, **kwargs) -> dict:
    with open_url(url, **kwargs) as resp:
        return json.load(resp)


def stream_lines(url: str, **kwargs) -> Iterator[bytes]:
    """Yield the response line by line without ever holding the whole body in memory."""
    with open_url(url, timeout=300, **kwargs) as resp:
        yield from resp
