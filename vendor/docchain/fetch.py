"""SSRF-hardened bounded HTTP fetch helpers for relayers and verifiers.

Doc Chain relayers routinely fetch operator-supplied URLs (publication
locations, node declarations, published reports) before spending gas or
trusting a claim. Every helper here treats the URL as hostile input:

- HTTPS only, and the host must not resolve to a private, loopback,
  link-local, or otherwise non-global address
- redirects are followed manually, each hop re-validated, and Authorization
  headers are stripped when a redirect leaves the original host
- every read is size-capped, including gzip decompression output

Caveat: the private-host check resolves DNS separately from the actual
request, so a DNS-rebinding attacker with a very short TTL can still race it.
Callers needing stronger guarantees should also restrict hosts with
`host_allowed`, which turns the check into an allowlist.

Raises `FetchError` (a ValueError) for policy violations and size overruns;
`urllib.error.HTTPError`/`URLError` propagate for transport failures so
callers can distinguish "refused by policy" from "failed on the wire".
"""

from __future__ import annotations

import gzip
import io
import ipaddress
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping

REDIRECT_STATUS_CODES = (301, 302, 303, 307, 308)
RETRYABLE_HTTP_STATUS_CODES = (408, 425, 429, 500, 502, 503, 504)


class FetchError(ValueError):
    """Raised when a fetch violates policy or exceeds a size bound."""


def read_limited(stream, limit: int, *, label: str = "download") -> bytes:
    """Read a stream to completion, raising once it exceeds `limit` bytes."""
    if limit < 1:
        raise FetchError(f"{label} size limit must be positive")
    chunks = []
    total = 0
    while True:
        chunk = stream.read(min(1024 * 1024, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise FetchError(f"{label} exceeds size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def gzip_decompress_limited(payload: bytes, limit: int, *, label: str = "gzip payload") -> bytes:
    """Decompress gzip bytes with a cap on the decompressed size."""
    with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as stream:
        return read_limited(stream, limit, label=label)


def strip_authorization_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if str(key).lower() != "authorization"}


def reject_private_host(host: str, *, label: str = "fetch") -> None:
    """Raise unless every address the host resolves to is globally routable."""
    try:
        addresses = [host]
        ipaddress.ip_address(host)
    except ValueError:
        try:
            addresses = [item[4][0] for item in socket.getaddrinfo(host, None)]
        except OSError as exc:
            raise FetchError(f"{label} host cannot be resolved: {host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or not ip.is_global
        ):
            raise FetchError(f"{label} host resolves to a non-public address")


def validate_fetch_url(
    url: str,
    *,
    host_allowed: Callable[[str], bool] | None = None,
    label: str = "fetch",
) -> None:
    """Enforce HTTPS, an optional host allowlist, and the private-host check."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise FetchError(f"{label} URL must use HTTPS")
    host = parsed.hostname
    if not host:
        raise FetchError(f"{label} URL is missing a host")
    host = host.lower()
    if host_allowed is not None and not host_allowed(host):
        raise FetchError(f"{label} URL host is not allowed")
    reject_private_host(host, label=label)


def http_error_retryable(exc: urllib.error.HTTPError) -> bool:
    return exc.code in RETRYABLE_HTTP_STATUS_CODES


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_url_bytes_with_redirects(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    headers: Mapping[str, str] | None = None,
    label: str = "fetch",
    host_allowed: Callable[[str], bool] | None = None,
    validate_url: Callable[[str], None] | None = None,
    allow_authorized_redirects: bool = False,
    max_redirects: int = 3,
) -> bytes:
    """Fetch a URL with manual, re-validated redirect handling.

    Every hop (including the first request) is checked with `validate_url`
    when given, otherwise with `validate_fetch_url(host_allowed=...)`. When a
    redirect leaves the original host, the Authorization header is dropped
    unless `allow_authorized_redirects` — a redirect must not exfiltrate
    credentials to an attacker-chosen host.
    """
    opener = urllib.request.build_opener(_NoRedirectHandler)
    base_headers = dict(headers or {})
    current = url
    origin_host = (urllib.parse.urlparse(url).hostname or "").lower()
    for _request in range(max_redirects + 1):
        if validate_url is not None:
            validate_url(current)
        else:
            validate_fetch_url(current, host_allowed=host_allowed, label=label)
        current_host = (urllib.parse.urlparse(current).hostname or "").lower()
        request_headers = dict(base_headers)
        if not allow_authorized_redirects and current_host != origin_host:
            request_headers = strip_authorization_headers(request_headers)
        request = urllib.request.Request(current, headers=request_headers)
        try:
            with opener.open(request, timeout=timeout) as response:
                return read_limited(response, max_bytes, label=label)
        except urllib.error.HTTPError as exc:
            if exc.code not in REDIRECT_STATUS_CODES:
                raise
            try:
                location = exc.headers.get("location")
                if not location:
                    raise FetchError(f"{label} redirect response is missing Location") from exc
                current = urllib.parse.urljoin(current, location)
            finally:
                exc.close()
    raise FetchError(f"{label} redirects too many times")


def fetch_url_bytes(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    headers: Mapping[str, str] | None = None,
    label: str = "fetch",
    host_allowed: Callable[[str], bool] | None = None,
    validate_url: Callable[[str], None] | None = None,
    allow_authorized_redirects: bool = False,
    max_redirects: int = 3,
    retries: int = 0,
    retry_delay: float = 1.0,
) -> bytes:
    """`fetch_url_bytes_with_redirects` with retries on transient failures.

    Retries only transport-level failures (retryable HTTP status codes,
    timeouts, connection errors); policy violations raise immediately.
    """
    for attempt in range(retries + 1):
        try:
            return fetch_url_bytes_with_redirects(
                url,
                timeout=timeout,
                max_bytes=max_bytes,
                headers=headers,
                label=label,
                host_allowed=host_allowed,
                validate_url=validate_url,
                allow_authorized_redirects=allow_authorized_redirects,
                max_redirects=max_redirects,
            )
        except urllib.error.HTTPError as exc:
            if not http_error_retryable(exc) or attempt == retries:
                raise FetchError(f"{label} failed for {url}: HTTP {exc.code}") from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            if attempt == retries:
                raise FetchError(f"{label} did not settle for {url}: {exc}") from exc
        time.sleep(retry_delay)
    raise FetchError(f"{label} did not settle for {url}")
