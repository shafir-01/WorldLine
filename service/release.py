"""Fetch a tagged release's evidence from the GitLab generic package registry.

Used by ``/api/verify/release``. Only HTTPS URLs on allowlisted hosts are fetched, redirects are
followed by hand (each hop re-checked against the allowlist), every response is capped at the
service's body limit and the whole fetch has one deadline. Results are cached briefly per tag
so a burst of identical requests does not turn into a burst of upstream calls.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

API_BASE = os.environ.get("WORLDLINE_RELEASE_API", "https://gitlab.com/api/v4").rstrip("/")
PROJECT_ID = os.environ.get("WORLDLINE_RELEASE_PROJECT", "88121109")
PACKAGE = os.environ.get("WORLDLINE_RELEASE_PACKAGE", "worldline-evidence")
ALLOWED_HOSTS = frozenset(
    h.strip().lower() for h in os.environ.get("WORLDLINE_RELEASE_HOSTS", "gitlab.com").split(",") if h.strip()
)
FETCH_TIMEOUT_S = 10.0
MAX_REDIRECTS = 3
CACHE_TTL_S = 300.0
CACHE_MAX = 32

TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_COMMIT_IN_NOTES = re.compile(r"Commit:\s*`([0-9a-f]{7,64})`")
_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")

# Tests swap this for httpx.MockTransport; None means the real network.
TRANSPORT: httpx.AsyncBaseTransport | None = None


class ReleaseError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class ReleaseEvidence:
    tag: str
    package_url: str
    bundle_url: str
    bundle_raw: bytes
    artifact_digest: str | None
    artifact_digest_source: str | None


_cache: dict[str, tuple[float, ReleaseEvidence]] = {}


def clear_cache() -> None:
    _cache.clear()


def package_url(tag: str) -> str:
    return f"{API_BASE}/projects/{PROJECT_ID}/packages/generic/{PACKAGE}/{tag}"


def release_api_url(tag: str) -> str:
    return f"{API_BASE}/projects/{PROJECT_ID}/releases/{tag}"


def check_url(url: str) -> None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in ALLOWED_HOSTS or parts.username or parts.password:
        raise ReleaseError(502, f"refusing to fetch {parts.scheme}://{host}: host not in allowlist")


async def _get(client: httpx.AsyncClient, url: str, max_bytes: int) -> bytes | None:
    """GET ``url``; None on 404; ReleaseError on anything else unexpected."""
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url)
        async with client.stream("GET", url) as resp:
            if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                url = urljoin(url, resp.headers["location"])
                continue
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                raise ReleaseError(502, f"upstream returned {resp.status_code} for {urlsplit(url).path}")
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ReleaseError(413, f"upstream file exceeds {max_bytes} bytes")
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf += chunk
                if len(buf) > max_bytes:
                    raise ReleaseError(413, f"upstream file exceeds {max_bytes} bytes")
            return bytes(buf)
    raise ReleaseError(502, "too many redirects")


def _json_or_none(raw: bytes | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _commit_from(release: Any, index: Any) -> tuple[str | None, str | None]:
    if isinstance(release, dict):
        commit = release.get("commit")
        if isinstance(commit, dict) and isinstance(commit.get("id"), str) and _SHA_RE.match(commit["id"]):
            return commit["id"], "release.commit"
        m = _COMMIT_IN_NOTES.search(str(release.get("description") or ""))
        if m:
            return m.group(1), "release_notes"
    if isinstance(index, dict) and isinstance(index.get("code_commit"), str) and _SHA_RE.match(index["code_commit"]):
        return index["code_commit"], "index.json"
    return None, None


async def fetch_release(tag: str, max_bytes: int) -> ReleaseEvidence:
    if not TAG_RE.match(tag) or ".." in tag:
        raise ReleaseError(400, f"invalid tag {tag!r}")
    hit = _cache.get(tag)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]

    base = package_url(tag)
    timeout = httpx.Timeout(FETCH_TIMEOUT_S)
    try:
        async with asyncio.timeout(FETCH_TIMEOUT_S):
            async with httpx.AsyncClient(transport=TRANSPORT, timeout=timeout, follow_redirects=False,
                                         headers={"user-agent": "worldline-verifier"}) as client:
                bundle_raw, release_raw, index_raw = await asyncio.gather(
                    _get(client, f"{base}/bundle.json", max_bytes),
                    _get(client, release_api_url(tag), 1024 * 1024),
                    _get(client, f"{base}/index.json", max_bytes),
                )
    except TimeoutError:
        raise ReleaseError(504, f"fetching release {tag} took longer than {FETCH_TIMEOUT_S:.0f}s") from None
    except httpx.HTTPError as exc:
        raise ReleaseError(502, f"fetching release {tag} failed: {type(exc).__name__}") from None

    if bundle_raw is None:
        raise ReleaseError(404, f"no evidence bundle published for tag {tag!r}")
    digest, source = _commit_from(_json_or_none(release_raw), _json_or_none(index_raw))
    ev = ReleaseEvidence(tag=tag, package_url=base, bundle_url=f"{base}/bundle.json", bundle_raw=bundle_raw,
                         artifact_digest=digest, artifact_digest_source=source)
    if len(_cache) >= CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[tag] = (time.monotonic(), ev)
    return ev
