from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Iterable

from .web_download import TRUSTED_REPOSITORY_BASE, DownloadError


CATALOG_FORMAT_VERSION = 1
CATALOG_PLACEHOLDER = "choose version"


@dataclass(frozen=True)
class CatalogRelease:
    id: str
    required_live_version: str | None = None


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


def _download_catalog(*, timeout: float) -> dict:
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

    return data


def parse_release_catalog(data: dict) -> list[CatalogRelease]:
    if not isinstance(data, dict):
        raise DownloadError("release catalog must contain a JSON object")
    if data.get("format_version") != CATALOG_FORMAT_VERSION:
        raise DownloadError(f"unsupported catalog version: {data.get('format_version')!r}")
    releases = data.get("releases")
    if not isinstance(releases, list):
        raise DownloadError("catalog releases must be a list")

    result: list[CatalogRelease] = []
    seen: set[str] = set()
    for item in releases:
        release_id = item.get("id") if isinstance(item, dict) else item
        if not isinstance(release_id, str):
            continue
        release_id = release_id.strip()
        if not release_id or release_id in seen:
            continue
        required_live_version = None
        if isinstance(item, dict):
            raw_required_version = item.get("required_live_version")
            if isinstance(raw_required_version, str):
                required_live_version = raw_required_version.strip() or None
        seen.add(release_id)
        result.append(
            CatalogRelease(
                id=release_id,
                required_live_version=required_live_version,
            )
        )
    return result


def fetch_release_catalog_details(*, timeout: float = 10.0) -> list[CatalogRelease]:
    """Fetch release IDs and optional pre-download compatibility metadata."""

    return parse_release_catalog(_download_catalog(timeout=timeout))


def fetch_release_catalog(*, timeout: float = 10.0) -> list[str]:
    """Backward-compatible release ID list for callers that only need choices."""

    return [release.id for release in fetch_release_catalog_details(timeout=timeout)]


def build_catalog(releases: Iterable[str | CatalogRelease]) -> dict:
    seen: set[str] = set()
    catalog_releases = []
    for value in releases:
        if isinstance(value, CatalogRelease):
            release_id = value.id.strip()
            required_live_version = str(value.required_live_version or "").strip() or None
        else:
            release_id = str(value).strip()
            required_live_version = None
        if not release_id or release_id in seen:
            continue
        seen.add(release_id)
        item = {"id": release_id}
        if required_live_version:
            item["required_live_version"] = required_live_version
        catalog_releases.append(item)
    return {
        "format_version": CATALOG_FORMAT_VERSION,
        "releases": catalog_releases,
    }
