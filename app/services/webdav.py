from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse
from xml.etree import ElementTree

import httpx


@dataclass(frozen=True)
class WebDAVItem:
    relative_path: str
    filename: str
    is_directory: bool
    etag: str | None
    last_modified: datetime | None
    content_length: int | None
    content_type: str | None


class WebDAVError(RuntimeError):
    pass


class WebDAVDownloadTooLarge(WebDAVError):
    pass


@dataclass(frozen=True)
class WebDAVDownload:
    sha256: str
    size: int


class WebDAVClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: float = 30):
        self.base_url = base_url.rstrip("/") + "/"
        self.base_path = urlparse(self.base_url).path.rstrip("/")
        self.auth = httpx.BasicAuth(username, password)
        self.timeout = timeout

    def _url(self, path: str) -> str:
        relative_path = self._relative_path(path, self.base_path)
        segments = [quote(part) for part in relative_path.strip("/").split("/") if part]
        if not segments:
            return self.base_url
        suffix = "/".join(segments) + ("/" if path.endswith("/") else "")
        return urljoin(self.base_url, suffix)

    async def list_directory(self, path: str) -> list[WebDAVItem]:
        body = """<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:displayname/><d:getetag/><d:getlastmodified/><d:getcontentlength/><d:getcontenttype/><d:resourcetype/></d:prop></d:propfind>"""
        async with httpx.AsyncClient(auth=self.auth, timeout=self.timeout, verify=True, follow_redirects=False) as client:
            response = await client.request("PROPFIND", self._url(path), headers={"Depth": "1", "Content-Type": "application/xml"}, content=body)
        if response.status_code != 207:
            raise WebDAVError(f"WebDAV-Verzeichnisabfrage fehlgeschlagen ({response.status_code})")
        return self.parse_multistatus(response.text, path, self.base_path)

    async def download_file(self, path: str) -> bytes:
        async with httpx.AsyncClient(auth=self.auth, timeout=self.timeout, verify=True, follow_redirects=False) as client:
            response = await client.get(self._url(path))
        if response.status_code != 200:
            raise WebDAVError(f"WebDAV-Download fehlgeschlagen ({response.status_code})")
        return response.content

    async def download_file_to_path(
        self,
        path: str,
        destination: Path,
        *,
        max_bytes: int | None = None,
    ) -> WebDAVDownload:
        """Stream a file to disk while hashing it, keeping large files out of RAM."""

        digest = hashlib.sha256()
        size = 0
        async with httpx.AsyncClient(auth=self.auth, timeout=self.timeout, verify=True, follow_redirects=False) as client:
            async with client.stream("GET", self._url(path)) as response:
                if response.status_code != 200:
                    raise WebDAVError(f"WebDAV-Download fehlgeschlagen ({response.status_code})")
                advertised = response.headers.get("content-length", "")
                if max_bytes is not None and advertised.isdigit() and int(advertised) > max_bytes:
                    raise WebDAVDownloadTooLarge(
                        f"Datei ist größer als das Verarbeitungslimit ({int(advertised)} > {max_bytes} Bytes)"
                    )
                with destination.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if max_bytes is not None and size > max_bytes:
                            raise WebDAVDownloadTooLarge(
                                f"Datei ist größer als das Verarbeitungslimit ({size} > {max_bytes} Bytes)"
                            )
                        handle.write(chunk)
                        digest.update(chunk)
        return WebDAVDownload(sha256=digest.hexdigest(), size=size)

    async def get_metadata(self, path: str) -> WebDAVItem | None:
        items = await self.list_directory(path)
        return items[0] if items else None

    @staticmethod
    def _relative_path(path: str, base_path: str = "") -> str:
        parsed_path = unquote(urlparse(path).path)
        normalized_base = "/" + base_path.strip("/") if base_path.strip("/") else ""
        if normalized_base and (parsed_path == normalized_base or parsed_path.startswith(normalized_base + "/")):
            parsed_path = parsed_path[len(normalized_base):]
        return "/" + parsed_path.strip("/") if parsed_path.strip("/") else "/"

    @staticmethod
    def parse_multistatus(xml: str, requested_path: str, base_path: str = "") -> list[WebDAVItem]:
        root = ElementTree.fromstring(xml)
        ns = {"d": "DAV:"}
        requested = WebDAVClient._relative_path(requested_path, base_path).rstrip("/") or "/"
        items: list[WebDAVItem] = []
        for response in root.findall("d:response", ns):
            href = unquote(response.findtext("d:href", default="", namespaces=ns))
            prop = response.find("d:propstat/d:prop", ns)
            if prop is None:
                continue
            parsed_path = WebDAVClient._relative_path(href, base_path).rstrip("/") or "/"
            if parsed_path == requested:
                continue
            resource_type = prop.find("d:resourcetype", ns)
            is_directory = resource_type is not None and resource_type.find("d:collection", ns) is not None
            modified_raw = prop.findtext("d:getlastmodified", namespaces=ns)
            try:
                modified = parsedate_to_datetime(modified_raw).astimezone(timezone.utc) if modified_raw else None
            except (TypeError, ValueError):
                modified = None
            length_raw = prop.findtext("d:getcontentlength", namespaces=ns)
            relative = parsed_path
            filename = parsed_path.rsplit("/", 1)[-1]
            items.append(WebDAVItem(
                relative_path=relative,
                filename=filename,
                is_directory=is_directory,
                etag=prop.findtext("d:getetag", namespaces=ns),
                last_modified=modified,
                content_length=int(length_raw) if length_raw and length_raw.isdigit() else None,
                content_type=prop.findtext("d:getcontenttype", namespaces=ns),
            ))
        return items

    async def walk_files(self, root_path: str) -> list[WebDAVItem]:
        pending = [root_path]
        files: list[WebDAVItem] = []
        while pending:
            current = pending.pop()
            for item in await self.list_directory(current):
                if item.is_directory:
                    pending.append(item.relative_path + "/")
                else:
                    files.append(item)
        return files
