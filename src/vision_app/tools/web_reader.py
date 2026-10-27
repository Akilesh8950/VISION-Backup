from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import urljoin, urlparse

from ..connectivity import internet_tool
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

    def handle_starttag(self, tag: str, _attrs) -> None:
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
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


def _resolve_public_addresses(url: str) -> tuple[Any, str, str, int, list[tuple[int, tuple[Any, ...]]]]:
    """Resolve and validate every candidate address for a URL host."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise PermissionError("Blocked by VISION safety policy: only HTTP/HTTPS URLs are allowed")
    if parsed.username or parsed.password:
        raise PermissionError("Blocked by VISION safety policy: URLs with embedded credentials are not allowed")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host or host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".localhost")):
        raise PermissionError("Blocked by VISION safety policy: local hosts are not allowed")
    try:
        host_ascii = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise PermissionError("Blocked by VISION safety policy: URL host is invalid") from exc
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise PermissionError("Blocked by VISION safety policy: URL port is invalid") from exc
    try:
        infos = socket.getaddrinfo(host_ascii, port, proto=socket.IPPROTO_TCP, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RuntimeError(f"Could not resolve host {host}") from exc

    addresses: list[tuple[int, tuple[Any, ...]]] = []
    seen: set[tuple[int, tuple[Any, ...]]] = set()
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        if not sockaddr or not _ip_is_public(str(sockaddr[0])):
            raise PermissionError("Blocked by VISION safety policy: URL resolves to a non-public address")
        key = (family, tuple(sockaddr))
        if key not in seen:
            seen.add(key)
            addresses.append((family, tuple(sockaddr)))
    if not addresses:
        raise PermissionError("Blocked by VISION safety policy: URL resolves to a non-public address")
    return parsed, host, host_ascii, port, addresses


def validate_public_url(url: str) -> str:
    _resolve_public_addresses(url)
    return url


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection that only dials pre-validated socket addresses."""

    def __init__(self, host: str, port: int, addresses: list[tuple[int, tuple[Any, ...]]], timeout: float):
        # HTTPConnection's host parsing handles a bracketed IPv6 literal but
        # not a bare one. The socket itself is pinned below and never comes
        # from a second DNS lookup.
        display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        super().__init__(display_host, port=port, timeout=timeout)
        self._pinned_addresses = addresses

    def _connect_pinned_socket(self) -> socket.socket:
        last_error: OSError | None = None
        for family, sockaddr in self._pinned_addresses:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            try:
                sock.connect(sockaddr)
                return sock
            except OSError as exc:
                last_error = exc
                sock.close()
        raise OSError("Could not connect to the validated public address") from last_error

    def connect(self) -> None:
        self.sock = self._connect_pinned_socket()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        port: int,
        addresses: list[tuple[int, tuple[Any, ...]]],
        timeout: float,
    ):
        display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        self._tls_context = ssl.create_default_context()
        super().__init__(display_host, port=port, timeout=timeout, context=self._tls_context)
        self._pinned_addresses = addresses
        self._tls_server_hostname = host

    def connect(self) -> None:
        raw = _PinnedHTTPConnection._connect_pinned_socket(self)  # type: ignore[arg-type]
        try:
            self.sock = self._tls_context.wrap_socket(raw, server_hostname=self._tls_server_hostname)
        except Exception:
            raw.close()
            raise


def _fetch_pinned(url: str) -> tuple[int, Any, bytes]:
    parsed, _host, host_ascii, port, addresses = _resolve_public_addresses(url)
    scheme = parsed.scheme.lower()
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query

    connection: http.client.HTTPConnection
    if scheme == "https":
        connection = _PinnedHTTPSConnection(host_ascii, port, addresses, timeout=20.0)
    else:
        connection = _PinnedHTTPConnection(host_ascii, port, addresses, timeout=20.0)
    try:
        connection.request(
            "GET",
            target,
            headers={
                "User-Agent": "VISION Desktop reader",
                "Accept": "text/html,text/plain,application/xhtml+xml;q=0.9,*/*;q=0.1",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        if response.status in {301, 302, 303, 307, 308, *range(400, 600)}:
            return response.status, response.msg, b""
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise RuntimeError("Page exceeds the 2 MB reader limit")
            chunks.append(chunk)
        return response.status, response.msg, b"".join(chunks)
    finally:
        connection.close()


def extract_readable(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.title, parser.text


@internet_tool
def read_url(args: schemas.ReadUrlArgs) -> dict:
    url = args.url
    content_type = ""
    raw = ""
    final_url = url

    for _ in range(6):
        # _fetch_pinned resolves once, validates every returned address,
        # and dials only those addresses. There is no second, attacker-
        # controlled DNS answer between validation and connection (the
        # classic DNS-rebinding TOCTOU in the previous httpx version).
        status, headers, body = _fetch_pinned(url)
        if status in {301, 302, 303, 307, 308}:
            location = headers.get("location") if headers is not None else None
            if not location:
                raise RuntimeError(f"HTTP {status}")
            url = urljoin(url, str(location))
            continue
        if status >= 400:
            raise RuntimeError(f"HTTP {status}")
        content_type = str(headers.get_content_type() if headers is not None else "").lower()
        raw_content_type = str(headers.get("content-type", "") if headers is not None else "").lower()
        if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
            raise RuntimeError(f"Unsupported content type for reader: {raw_content_type or 'unknown'}")
        charset = "utf-8"
        if headers is not None:
            try:
                charset = headers.get_content_charset() or charset
            except Exception:
                charset = "utf-8"
        try:
            raw = body.decode(charset, errors="replace")
        except LookupError:
            raw = body.decode("utf-8", errors="replace")
        final_url = url
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
