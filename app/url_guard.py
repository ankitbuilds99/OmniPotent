"""Where the server is allowed to send a request a user asked it to send.

A status-page webhook is a URL a subscriber hands us, which the server then
POSTs to from inside the deployment network. Unvalidated, that is a server-side
request forgery primitive: `http://169.254.169.254/latest/api/token` reaches the
cloud metadata endpoint, `http://127.0.0.1:8000/admin` reaches the app's own
internal surface, and `http://10.0.0.5:6379/` reaches whatever is on the private
network — all with the deployment's own network position, which the caller does
not have.

The control here is a deny-list of destinations, which is the opposite of the
allow-list guarding knowledge-graph sync sources in
`app/knowledge_graph/api_router.py`. The targets are what differ. A sync source
is the operator's own Prometheus, normally at `10.x` or `localhost`, so only an
operator may name one and blocking private ranges would block the correct
configuration. A webhook subscriber is a customer whose endpoint is by nature
arbitrary and external, so nobody can enumerate them in advance — but "not the
inside of our own network" is exactly right.

`COPILOT_WEBHOOK_ALLOWED_HOSTS` reopens named hosts for the on-prem deployment
whose subscribers genuinely are internal.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from app.config import get_settings

_DNS_TIMEOUT_S = 5.0


class BlockedURL(ValueError):
    """A URL this deployment must not fetch.

    The message names the host and the reason, and is safe to return to the
    caller: it tells them nothing they did not already supply.
    """


def _unwrap(ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    """The address actually routed to.

    `::ffff:169.254.169.254` and `2002:a9fe:a9fe::` are IPv6 spellings of IPv4
    addresses; a naive check on the IPv6 object misses them, because the v6
    address itself is global.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        return ip.ipv4_mapped or ip.sixtofour or ip
    return ip


def _blocked_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """Why this address is off limits, or "" if it is a fine place to send to."""
    addr = _unwrap(ip)
    if addr.is_unspecified:
        return "an unspecified address"
    if addr.is_loopback:
        return "a loopback address"
    if addr.is_link_local:
        return "a link-local address (this is where cloud metadata lives)"
    if addr.is_multicast:
        return "a multicast address"
    if addr.is_reserved:
        return "a reserved address"
    if addr.is_private:
        return "a private address"
    return ""


def _allowed_hosts() -> set[str]:
    raw = get_settings().copilot_webhook_allowed_hosts
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _is_allow_listed(host: str, port: int | None) -> bool:
    allowed = _allowed_hosts()
    if not allowed:
        return False
    return bool({host, f"{host}:{port}" if port else host} & allowed)


async def _resolve(host: str, port: int) -> list[str]:
    """Every address `host` resolves to.

    All of them, not just the first: a name that returns one public and one
    private address would otherwise pass validation and then be connected to on
    whichever one the client picked.
    """
    def _lookup() -> list[str]:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        return [str(info[4][0]) for info in infos]

    return await asyncio.wait_for(asyncio.to_thread(_lookup), timeout=_DNS_TIMEOUT_S)


async def assert_public_url(url: str, *, field: str = "url") -> None:
    """Raise `BlockedURL` unless `url` points somewhere outside this network.

    Checks the shape first (an http(s) URL with a host), then resolves the host
    and rejects the request if *any* address it answers with is loopback,
    link-local, private, reserved, multicast or unspecified.

    A host that does not resolve is *not* rejected. This function answers "may we
    send there", and a name with no address is not a destination — a connection
    to it fails on its own. Rejecting would also mean an air-gapped or offline
    deployment could not accept a webhook at all, and it would make the answer
    depend on whether DNS happened to be up.

    Call this again immediately before sending, not only when the URL is
    accepted. A name that resolved to a public address at subscribe time can
    resolve to 127.0.0.1 an hour later; re-checking narrows that window to the
    gap between this call and the connection, which is as far as validation can
    go without also pinning the socket to the address that was checked.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise BlockedURL(f"{field} must be an http(s) URL")
    host = (parts.hostname or "").lower()
    if not host:
        raise BlockedURL(f"{field} must include a host")

    try:
        port = parts.port
    except ValueError:
        raise BlockedURL(f"{field} has an invalid port") from None
    port = port or (443 if parts.scheme == "https" else 80)

    if _is_allow_listed(host, parts.port):
        return

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        addresses = [host]
    else:
        try:
            addresses = await _resolve(host, port)
        except (socket.gaierror, UnicodeError, TimeoutError, asyncio.TimeoutError):
            return  # nothing resolves, so there is nowhere to send — see above

    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw.split("%", 1)[0])  # strip any zone id
        except ValueError:
            continue
        reason = _blocked_reason(ip)
        if reason:
            raise BlockedURL(
                f"{field} host '{host}' resolves to {reason} ({ip}), which this "
                "deployment will not send requests to. Set "
                "COPILOT_WEBHOOK_ALLOWED_HOSTS if this endpoint really is internal."
            )


async def is_public_url(url: str) -> bool:
    """`assert_public_url` as a predicate, for delivery-time re-checks."""
    try:
        await assert_public_url(url)
    except BlockedURL:
        return False
    return True
