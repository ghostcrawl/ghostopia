"""SSRF gate for user-submitted mission target URLs.

Every URL a mission/task would dispatch against is validated HERE before any GhostCrawl
call. Even though GhostCrawl egress is itself proxy-mediated, ghostopia must still refuse
private/loopback/link-local/metadata targets at its own boundary, so a user (or an
AI-authored task) cannot pivot the server
into the internal network or the cloud metadata endpoint (169.254.169.254).

Policy:
  * scheme MUST be http or https (no file/ftp/gopher/ws/javascript/…);
  * the host is resolved to IP(s) and EVERY resolved address must be a global/public
    address — any loopback / private / link-local / reserved / multicast / unspecified
    address blocks the URL;
  * the configured self-host target host(s) are an EXPLICIT, SCOPED allow
    — allowing ``localhost`` does NOT open other private ranges, only that exact host.

The DNS resolver is injectable (``resolver=``) so the gate is testable without real DNS
and so a caller can supply a pinned resolver; the default uses ``socket.getaddrinfo``.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

Resolver = Callable[[str], list[str]]

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class SsrfBlockedError(ValueError):
    """Raised when a mission target URL is refused by the SSRF policy."""


def _default_resolver(host: str) -> list[str]:
    """Resolve ``host`` to its IP addresses via the system resolver."""
    infos = socket.getaddrinfo(host, None)
    # ``sockaddr[0]`` is the numeric address for both AF_INET and AF_INET6.
    return [str(info[4][0]) for info in infos]


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True when ``ip`` is anything but a globally-routable public address.

    Covers loopback (127/8, ::1), RFC1918 private (10/8, 172.16/12, 192.168/16, fc00::/7),
    link-local (169.254/16 incl. the 169.254.169.254 metadata endpoint, fe80::/10),
    reserved, multicast, and the unspecified address.
    """
    # Normalize IPv4-mapped IPv6 (e.g. ::ffff:10.0.0.1) to its v4 form first.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_mission_url(
    url: str,
    allowed_self_host_hosts: tuple[str, ...] = (),
    *,
    resolver: Resolver | None = None,
) -> str:
    """Validate a user-submitted mission target URL, returning it unchanged if allowed.

    Raises ``SsrfBlockedError`` for a non-http(s) scheme, a missing host, an unresolvable
    host, or ANY resolved address in a blocked range. ``allowed_self_host_hosts`` is the
    scoped self-host exception (matched case-insensitively against the URL host); it does
    NOT relax the range checks for any other host.
    """
    if not url:
        raise SsrfBlockedError("empty URL")

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SsrfBlockedError(f"scheme {scheme!r} is not allowed (http/https only)")

    host = parts.hostname
    if not host:
        raise SsrfBlockedError(f"URL has no host: {url!r}")

    host_lower = host.lower()

    # Explicit, scoped self-host allowance. Only this exact host is exempted.
    allowed = {h.lower() for h in allowed_self_host_hosts}
    if host_lower in allowed:
        return url

    # Determine the addresses to check: IP-literal hosts are checked directly (no DNS);
    # hostnames are resolved and EVERY address must be public.
    candidate_ips: list[str]
    try:
        ipaddress.ip_address(host.strip("[]"))
        candidate_ips = [host.strip("[]")]
    except ValueError:
        resolve = resolver or _default_resolver
        try:
            candidate_ips = resolve(host)
        except OSError as exc:
            raise SsrfBlockedError(f"could not resolve host {host!r}: {exc}") from exc
        if not candidate_ips:
            raise SsrfBlockedError(f"host {host!r} resolved to no addresses") from None

    for raw in candidate_ips:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise SsrfBlockedError(f"invalid resolved address {raw!r}: {exc}") from exc
        if _is_blocked_ip(ip):
            raise SsrfBlockedError(
                f"target {url!r} resolves to blocked address {raw} "
                "(private/loopback/link-local/metadata)"
            )

    return url
