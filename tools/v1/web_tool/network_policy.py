from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

from .config import (
    DNS_TIMEOUT_SECONDS,
    MAX_DNS_ADDRESSES,
    MAX_HOST_CHARS,
    MAX_PROXY_URL_CHARS,
    MAX_URL_CHARS,
)
from .errors import WebToolError


Resolver = Callable[[str, int], Awaitable[list[str]]]


@dataclass(frozen=True)
class ResolvedTarget:
    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]


def _canonical_ip_text(value: str) -> str:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address)


def _is_global_ip_text(value: str) -> bool:
    try:
        address = ipaddress.ip_address(_canonical_ip_text(value))
    except ValueError:
        return False
    return bool(address.is_global)


def _dedupe_bounded(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) > MAX_DNS_ADDRESSES:
            raise WebToolError(
                "WEB_DNS_FAILED",
                "DNS returned too many addresses",
                details={"max_addresses": MAX_DNS_ADDRESSES},
            )
    return tuple(result)


async def _system_resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(
                host,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            ),
            timeout=DNS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise WebToolError(
            "WEB_DNS_FAILED",
            "DNS resolution timed out",
            retryable=True,
            details={"host": host},
        ) from exc
    except socket.gaierror as exc:
        raise WebToolError(
            "WEB_DNS_FAILED",
            "DNS resolution failed",
            retryable=True,
            details={"host": host, "exception_type": type(exc).__name__},
        ) from exc
    return [str(info[4][0]) for info in infos]


class NetworkPolicy:
    def __init__(self, resolver: Resolver | None = None) -> None:
        self._resolver = resolver or _system_resolve

    @staticmethod
    def normalize_url(url: str) -> tuple[str, str, int]:
        if not isinstance(url, str) or not url.strip():
            raise WebToolError("INVALID_ARGUMENT", "url must be a non-empty string")
        candidate = url.strip()
        if len(candidate) > MAX_URL_CHARS:
            raise WebToolError(
                "INVALID_ARGUMENT",
                f"url exceeds maximum length {MAX_URL_CHARS}",
            )
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in candidate):
            raise WebToolError("INVALID_ARGUMENT", "url contains control characters")

        try:
            parts = urlsplit(candidate)
        except Exception as exc:
            raise WebToolError(
                "INVALID_ARGUMENT",
                "url could not be parsed",
                details={"exception_type": type(exc).__name__},
            ) from exc

        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"}:
            raise WebToolError("WEB_URL_BLOCKED", "only http and https URLs are allowed")
        if parts.username is not None or parts.password is not None:
            raise WebToolError("WEB_URL_BLOCKED", "URL credentials are not allowed")

        host = parts.hostname
        if not host:
            raise WebToolError("INVALID_ARGUMENT", "url hostname is required")
        host = host.rstrip(".").lower()
        if not host or len(host) > MAX_HOST_CHARS:
            raise WebToolError("INVALID_ARGUMENT", "url hostname is invalid")
        if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
            raise WebToolError("WEB_URL_BLOCKED", "local hostnames are not allowed")

        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise WebToolError("INVALID_ARGUMENT", "url hostname is not valid IDNA") from exc
        if not ascii_host or len(ascii_host) > MAX_HOST_CHARS:
            raise WebToolError("INVALID_ARGUMENT", "url hostname is invalid")

        try:
            explicit_port = parts.port
        except ValueError as exc:
            raise WebToolError("INVALID_ARGUMENT", "url port is invalid") from exc
        port = explicit_port if explicit_port is not None else (443 if scheme == "https" else 80)
        if type(port) is not int or port < 1 or port > 65535:
            raise WebToolError("INVALID_ARGUMENT", "url port is outside 1..65535")

        host_for_netloc = f"[{ascii_host}]" if ":" in ascii_host and not ascii_host.startswith("[") else ascii_host
        default_port = 443 if scheme == "https" else 80
        netloc = host_for_netloc if port == default_port else f"{host_for_netloc}:{port}"
        normalized = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
        return normalized, ascii_host, port

    async def resolve_proxy(self, proxy_url: str) -> ResolvedTarget:
        if not isinstance(proxy_url, str) or not proxy_url.strip():
            raise WebToolError(
                "INVALID_ARGUMENT",
                "proxy URL must be a non-empty string",
            )
        candidate = proxy_url.strip()
        if len(candidate) > MAX_PROXY_URL_CHARS:
            raise WebToolError(
                "INVALID_ARGUMENT",
                "proxy URL exceeds its hard length limit",
            )
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in candidate):
            raise WebToolError(
                "INVALID_ARGUMENT",
                "proxy URL contains control characters",
            )

        try:
            parts = urlsplit(candidate)
        except Exception as exc:
            raise WebToolError(
                "INVALID_ARGUMENT",
                "proxy URL could not be parsed",
                details={"exception_type": type(exc).__name__},
            ) from exc

        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"} or not parts.hostname:
            raise WebToolError(
                "INVALID_ARGUMENT",
                "proxy URL must use http or https with a hostname",
            )
        if parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise WebToolError(
                "INVALID_ARGUMENT",
                "proxy URL must not contain a path, query, or fragment",
            )

        host = parts.hostname.rstrip(".").lower()
        if not host or len(host) > MAX_HOST_CHARS:
            raise WebToolError("INVALID_ARGUMENT", "proxy hostname is invalid")
        if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
            raise WebToolError(
                "WEB_URL_BLOCKED",
                "local proxy hostnames are not allowed",
            )
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise WebToolError(
                "INVALID_ARGUMENT",
                "proxy hostname is not valid IDNA",
            ) from exc
        if not ascii_host or len(ascii_host) > MAX_HOST_CHARS:
            raise WebToolError("INVALID_ARGUMENT", "proxy hostname is invalid")

        try:
            explicit_port = parts.port
        except ValueError as exc:
            raise WebToolError("INVALID_ARGUMENT", "proxy URL port is invalid") from exc
        port = explicit_port if explicit_port is not None else (
            443 if scheme == "https" else 80
        )
        if type(port) is not int or port < 1 or port > 65535:
            raise WebToolError(
                "INVALID_ARGUMENT",
                "proxy URL port is outside 1..65535",
            )

        addresses = await self.resolve_host(ascii_host, port)
        host_for_netloc = (
            f"[{ascii_host}]"
            if ":" in ascii_host and not ascii_host.startswith("[")
            else ascii_host
        )
        normalized = urlunsplit(
            (scheme, f"{host_for_netloc}:{port}", "", "", "")
        )
        return ResolvedTarget(
            url=normalized,
            scheme=scheme,
            host=ascii_host,
            port=port,
            addresses=addresses,
        )

    async def resolve_host(self, host: str, port: int) -> tuple[str, ...]:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None

        if literal is not None:
            addresses = (str(literal),)
        else:
            try:
                raw = await self._resolver(host, port)
            except asyncio.CancelledError:
                raise
            except WebToolError:
                raise
            except Exception as exc:
                raise WebToolError(
                    "WEB_DNS_FAILED",
                    "DNS resolution failed",
                    retryable=True,
                    details={
                        "host": host,
                        "exception_type": type(exc).__name__,
                    },
                ) from exc
            addresses = _dedupe_bounded(raw)

        if not addresses:
            raise WebToolError(
                "WEB_DNS_FAILED",
                "DNS returned no addresses",
                retryable=True,
                details={"host": host},
            )

        blocked = [value for value in addresses if not _is_global_ip_text(value)]
        if blocked:
            raise WebToolError(
                "WEB_URL_BLOCKED",
                "target resolves to a non-global network address",
                details={"host": host, "blocked_address_count": len(blocked)},
            )
        return addresses

    async def resolve_url(self, url: str) -> ResolvedTarget:
        normalized, host, port = self.normalize_url(url)
        addresses = await self.resolve_host(host, port)

        scheme = urlsplit(normalized).scheme
        return ResolvedTarget(
            url=normalized,
            scheme=scheme,
            host=host,
            port=port,
            addresses=addresses,
        )

    async def resolve_redirect(self, current_url: str, location: str) -> ResolvedTarget:
        if not isinstance(location, str) or not location.strip():
            raise WebToolError("WEB_HTTP_ERROR", "redirect Location header is missing")
        if len(location) > MAX_URL_CHARS:
            raise WebToolError("WEB_REDIRECT_BLOCKED", "redirect Location header is too long")
        try:
            next_url = urljoin(current_url, location.strip())
            return await self.resolve_url(next_url)
        except WebToolError as exc:
            if exc.code in {"WEB_URL_BLOCKED", "INVALID_ARGUMENT"}:
                raise WebToolError(
                    "WEB_REDIRECT_BLOCKED",
                    "redirect destination is blocked",
                    details={"reason": exc.code},
                ) from exc
            raise

    @staticmethod
    def verify_primary_ip(primary_ip: str | None, target: ResolvedTarget) -> None:
        if not isinstance(primary_ip, str) or not primary_ip:
            raise WebToolError(
                "WEB_NETWORK_ERROR",
                "network response did not expose its connected IP",
                retryable=True,
            )
        try:
            normalized = _canonical_ip_text(primary_ip)
        except ValueError as exc:
            raise WebToolError(
                "WEB_NETWORK_ERROR",
                "network response exposed an invalid connected IP",
                retryable=True,
            ) from exc
        if not _is_global_ip_text(normalized):
            raise WebToolError("WEB_URL_BLOCKED", "connected IP is not globally routable")
        allowed = {_canonical_ip_text(value) for value in target.addresses}
        if normalized not in allowed:
            raise WebToolError(
                "WEB_NETWORK_ERROR",
                "connected IP did not match the validated DNS set",
                retryable=True,
                details={"host": target.host},
            )
