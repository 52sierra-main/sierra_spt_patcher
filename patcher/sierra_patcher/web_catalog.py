from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable

from .web_download import TRUSTED_REPOSITORY_BASE, DownloadError


CATALOG_FORMAT_VERSION = 1
CATALOG_PLACEHOLDER = "choose version"


class _TrustedCatalogRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_host: str):
        super().__init__()
        self.allowed_host = allowed_host.lower()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() != self.allowed_host:
            raise DownloadError(f"refusing redirect outside trusted host: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def catalog_url() -> str:
    base = TRUSTED_REPOSITORY_BASE.rstrip("/") + "/"
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise RuntimeError("TRUSTED_REPOSITORY_BASE must be an HTTPS URL")
    return urllib.parse.urljoin(base, "catalog.json")


def fetch_release_catalog(*, timeout: float = 10.0) -> list[str]:
    """Fetch the lightweight release index without downloading any manifests."""
    url = catalog_url()
    parsed = urllib.parse.urlparse(url)
    opener = urllib.request.build_opener(_TrustedCatalogRedirectHandler(parsed.hostname or ""))
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SierraPatcher/1 catalog",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
        method="GET",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise DownloadError(
                "release catalog is not available on the repository (catalog.json missing)"
            ) from exc
        raise DownloadError(f"HTTP error {exc.code} while fetching release catalog") from exc
    except OSError as exc:
        raise DownloadError(f"could not fetch release catalog: {exc}") from exc

    if len(raw) > 1024 * 1024:
        raise DownloadError("release catalog is unexpectedly large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise DownloadError("release catalog is not valid JSON") from exc

    if data.get("format_version") != CATALOG_FORMAT_VERSION:
        raise DownloadError(f"unsupported catalog version: {data.get('format_version')!r}")
    releases = data.get("releases")
    if not isinstance(releases, list):
        raise DownloadError("catalog releases must be a list")

    result: list[str] = []
    seen: set[str] = set()
    for item in releases:
        release_id = item.get("id") if isinstance(item, dict) else item
        if not isinstance(release_id, str):
            continue
        release_id = release_id.strip()
        if not release_id or release_id in seen:
            continue
        seen.add(release_id)
        result.append(release_id)
    return result


def build_catalog(release_ids: Iterable[str]) -> dict:
    seen: set[str] = set()
    releases = []
    for value in release_ids:
        release_id = str(value).strip()
        if not release_id or release_id in seen:
            continue
        seen.add(release_id)
        releases.append({"id": release_id})
    return {
        "format_version": CATALOG_FORMAT_VERSION,
        "releases": releases,
    }
