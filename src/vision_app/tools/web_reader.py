from __future__ import annotations

import ipaddress
import socket
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import ClassVar
from urllib.parse import urljoin, urlparse

import httpx

from . import schemas

MAX_RESPONSE_BYTES = 2_000_000


class _TextExtractor(HTMLParser):
    SKIP: ClassVar[set[str]] = {"script", "style", "noscript", "svg", "canvas", "iframe"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self.SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "tr", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        self.parts.append(data)

    @property
    def title(self) -> str:
        return " ".join("".join(self.title_parts).split())

    @property
    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line)


def _ip_is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


def validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise PermissionError("Blocked by VISION safety policy: only HTTP/HTTPS URLs are allowed")
    if parsed.username or parsed.password:
        raise PermissionError("Blocked by VISION safety policy: URLs with embedded credentials are not allowed")
    host = (parsed.hostname or "").strip().lower()
    if not host or host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise PermissionError("Blocked by VISION safety policy: local hosts are not allowed")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise RuntimeError(f"Could not resolve host {host}") from exc
    addresses = {str(info[4][0]) for info in infos}
    if not addresses or any(not _ip_is_public(address) for address in addresses):
        raise PermissionError("Blocked by VISION safety policy: URL resolves to a non-public address")
    return url


def extract_readable(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.title, parser.text


def read_url(args: schemas.ReadUrlArgs) -> dict:
    url = args.url
    timeout = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
    with httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": "VISION Desktop reader"}) as client:
        for _ in range(6):
            validate_public_url(url)
            with client.stream("GET", url) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        response.raise_for_status()
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if not any(kind in content_type for kind in ("text/html", "text/plain", "application/xhtml+xml")):
                    raise RuntimeError(f"Unsupported content type for reader: {content_type or 'unknown'}")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes(64 * 1024):
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise RuntimeError("Page exceeds the 2 MB reader limit")
                    chunks.append(chunk)
                raw = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                final_url = str(response.url)
                break
        else:
            raise RuntimeError("Too many redirects")

    if "html" in content_type:
        title, text = extract_readable(raw)
    else:
        title, text = "", raw
    truncated = len(text) > args.max_chars
    return {
        "url": args.url,
        "final_url": final_url,
        "title": title,
        "text": text[:args.max_chars],
        "truncated": truncated,
        "fetched_at": datetime.now(UTC).isoformat(),
    }
