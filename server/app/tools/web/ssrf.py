"""SSRF guard for the web tool (T094, spec §31).

A web fetch is a request the *server* makes from inside its own network, so an
unchecked URL turns a tool call into a way to read cloud metadata
(``169.254.169.254``), reach an internal service on loopback, or probe the LAN.
§31 requires the guard, and §59.4's "resolve before you trust" applies here too:
the host is resolved to addresses *before* the request, and literally none of
them may be private, loopback, link-local or otherwise non-global. Redirects are
re-checked hop by hop by the caller because a public URL may redirect to a
private one.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import SplitResult, urlsplit

from app.core.errors import InvalidInputError, SsrfBlockedError

__all__ = ["assert_host_allowed", "assert_url_allowed", "parse_url"]

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def parse_url(url: str) -> SplitResult:
    """Split a URL, insisting on an ``http(s)`` scheme and a host."""
    if not isinstance(url, str) or not url.strip():
        raise InvalidInputError("url must be a non-empty string")
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise InvalidInputError(
            "url must use http or https", details={"scheme": parts.scheme or "(none)"}
        )
    if not parts.hostname:
        raise InvalidInputError("url must include a host")
    return parts


def assert_host_allowed(host: str, *, allow_private_networks: bool) -> None:
    """Refuse a host that resolves to a non-global address (unless allowed).

    A name that does not resolve at all is refused as well: it is more likely an
    internal name than a public one, and failing closed is the point of §31.
    """
    if allow_private_networks:
        return
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SsrfBlockedError(host) from exc
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise SsrfBlockedError(host)
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise SsrfBlockedError(host) from exc
        if not ip.is_global:
            raise SsrfBlockedError(host)


def assert_url_allowed(url: str, *, allow_private_networks: bool) -> str:
    """Validate ``url``'s scheme and host; return the cleaned URL string."""
    parts = parse_url(url)
    host = parts.hostname or ""
    assert_host_allowed(host, allow_private_networks=allow_private_networks)
    return url.strip()
