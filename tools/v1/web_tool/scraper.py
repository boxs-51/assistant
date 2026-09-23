from __future__ import annotations

import asyncio
import email.utils
import ipaddress
import time
from typing import Any, Optional
from urllib.parse import unquote, urlsplit, urlunsplit

try:
    from curl_cffi import CurlOpt
    from curl_cffi.requests import AsyncSession
except ImportError:
    CurlOpt = None
    AsyncSession = None

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

from .config import (
    MAX_REDIRECTS,
    MAX_RENDERED_HTML_CHARS,
    MAX_RETRY_AFTER_SECONDS,
    MAX_STATIC_RESPONSE_BYTES,
)
from .errors import WebToolError, dependency_error
from .extractors import extract_tables_and_charts
from .network_policy import NetworkPolicy, ResolvedTarget
from .stealth import WebToolStealth


_RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}
_REDIRECT_HTTP = {301, 302, 303, 307, 308}
_ALLOWED_BROWSER_LOCAL_SCHEMES = {"about", "data", "blob"}
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}


def remaining_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise WebToolError("WEB_TIMEOUT", "web operation timed out", retryable=True)
    return remaining


def retry_after_seconds(headers: Any) -> float | None:
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:
        return None
    if not raw:
        return None
    try:
        seconds = float(raw)
        if 0 <= seconds <= MAX_RETRY_AFTER_SECONDS:
            return seconds
        return None
    except (TypeError, ValueError):
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(str(raw))
        now = time.time()
        seconds = parsed.timestamp() - now
        if 0 <= seconds <= MAX_RETRY_AFTER_SECONDS:
            return seconds
    except Exception:
        return None
    return None


class WebScraper:
    def __init__(
        self,
        *,
        network_policy: NetworkPolicy,
        max_response_bytes: int = MAX_STATIC_RESPONSE_BYTES,
        max_concurrency: int = 5,
        session_factory: Any = None,
        playwright_factory: Any = None,
    ) -> None:
        self.max_response_bytes = max_response_bytes
        self._policy = network_policy
        self._session_factory = session_factory or AsyncSession
        self._playwright_factory = playwright_factory or async_playwright
        self._request_semaphore = asyncio.Semaphore(max_concurrency)
        self._browser_semaphore = asyncio.Semaphore(max_concurrency)
        self._lifecycle_lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        self._owner_loop: Optional[asyncio.AbstractEventLoop] = None

    async def __aenter__(self) -> "WebScraper":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owner_loop is not None and asyncio.get_running_loop() is not self._owner_loop:
            raise WebToolError(
                "WEB_LOOP_OWNERSHIP_VIOLATION",
                "Playwright resources must be closed on their owner event loop",
            )

        async with self._lifecycle_lock:
            browser = self._browser
            playwright = self._playwright
            self._browser = None
            self._playwright = None
            self._owner_loop = None

            first_error: Exception | None = None
            control_error: BaseException | None = None
            if browser is not None:
                try:
                    await browser.close()
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit) as exc:
                    control_error = exc
                except Exception as exc:
                    first_error = exc
            if playwright is not None:
                try:
                    await playwright.stop()
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit) as exc:
                    if control_error is None:
                        control_error = exc
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
            if control_error is not None:
                raise control_error
            if first_error is not None:
                raise WebToolError(
                    "WEB_CLEANUP_FAILED",
                    "Playwright resources could not be closed cleanly",
                    details={"exception_type": type(first_error).__name__},
                ) from first_error

    async def _get_browser(self) -> Any:
        async with self._lifecycle_lock:
            current_loop = asyncio.get_running_loop()
            if self._owner_loop is not None and current_loop is not self._owner_loop:
                raise WebToolError(
                    "WEB_LOOP_OWNERSHIP_VIOLATION",
                    "Playwright resources cannot cross event-loop ownership",
                )
            if self._browser is not None:
                try:
                    if self._browser.is_connected():
                        return self._browser
                except Exception:
                    pass

            if self._playwright_factory is None:
                raise dependency_error("playwright")

            if self._playwright is None:
                try:
                    playwright = await self._playwright_factory().start()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    raise WebToolError(
                        "WEB_BROWSER_START_FAILED",
                        "Playwright could not be started",
                        details={"exception_type": type(exc).__name__},
                    ) from exc
                self._playwright = playwright
                self._owner_loop = current_loop

            try:
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit) as exc:
                playwright = self._playwright
                self._playwright = None
                self._owner_loop = None
                if playwright is not None:
                    try:
                        await playwright.stop()
                    except Exception:
                        pass
                raise exc
            except Exception as exc:
                playwright = self._playwright
                self._playwright = None
                self._owner_loop = None
                cleanup_error: Exception | None = None
                if playwright is not None:
                    try:
                        await playwright.stop()
                    except Exception as stop_exc:
                        cleanup_error = stop_exc
                if cleanup_error is not None:
                    raise WebToolError(
                        "WEB_CLEANUP_FAILED",
                        "Playwright could not be stopped after browser launch failure",
                        details={
                            "exception_type": type(cleanup_error).__name__,
                        },
                    ) from cleanup_error
                raise WebToolError(
                    "WEB_BROWSER_START_FAILED",
                    "Chromium could not be launched",
                    details={"exception_type": type(exc).__name__},
                ) from exc
            return self._browser

    @staticmethod
    def _curl_options(target: ResolvedTarget) -> dict[Any, Any]:
        if CurlOpt is None:
            return {}
        try:
            ipaddress.ip_address(target.host)
            return {}
        except ValueError:
            pass
        addresses = ",".join(
            f"[{address}]" if ":" in address else address
            for address in target.addresses
        )
        return {
            CurlOpt.RESOLVE: [
                f"{target.host}:{target.port}:{addresses}"
            ]
        }

    @staticmethod
    def _content_type_allowed(content_type: str) -> bool:
        value = (content_type or "").split(";", 1)[0].strip().lower()
        return (
            value.startswith("text/")
            or value in {
                "application/json",
                "application/xml",
                "application/xhtml+xml",
            }
            or value.endswith("+json")
            or value.endswith("+xml")
        )

    @staticmethod
    async def _await_deadline(
        awaitable: Any,
        deadline: float,
        *,
        stage: str,
    ) -> Any:
        try:
            timeout = remaining_timeout(deadline)
        except WebToolError:
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            elif isinstance(awaitable, asyncio.Future):
                awaitable.cancel()
            raise
        try:
            return await asyncio.wait_for(
                awaitable,
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise WebToolError(
                "WEB_TIMEOUT",
                "web operation exceeded its total timeout",
                retryable=True,
                details={"stage": stage},
            ) from exc

    @staticmethod
    def _playwright_proxy_settings(
        proxy: str,
        *,
        resolved_target: ResolvedTarget | None = None,
    ) -> dict[str, str]:
        try:
            parts = urlsplit(proxy)
            host = parts.hostname
            if not host:
                raise ValueError("missing proxy hostname")
            port = parts.port
        except Exception as exc:
            raise WebToolError(
                "INVALID_ARGUMENT",
                "proxy URL could not be converted for Playwright",
                details={"exception_type": type(exc).__name__},
            ) from exc

        default_port = 443 if parts.scheme.lower() == "https" else 80
        actual_port = (
            resolved_target.port
            if resolved_target is not None
            else (port if port is not None else default_port)
        )
        connect_host = (
            resolved_target.addresses[0]
            if resolved_target is not None
            else host
        )
        host_text = f"[{connect_host}]" if ":" in connect_host else connect_host
        server = urlunsplit(
            (
                parts.scheme.lower(),
                f"{host_text}:{actual_port}",
                "",
                "",
                "",
            )
        )
        settings = {"server": server}
        if parts.username is not None:
            settings["username"] = unquote(parts.username)
        if parts.password is not None:
            settings["password"] = unquote(parts.password)
        return settings

    async def _read_bounded_response(self, response: Any) -> str:
        headers = getattr(response, "headers", {}) or {}
        declared = headers.get("Content-Length")
        if declared:
            try:
                if int(declared) > self.max_response_bytes:
                    raise WebToolError(
                        "WEB_RESPONSE_TOO_LARGE",
                        "web response exceeds the hard byte limit",
                        details={"max_bytes": self.max_response_bytes},
                    )
            except ValueError:
                pass

        content_type = str(headers.get("Content-Type", ""))
        if not self._content_type_allowed(content_type):
            raise WebToolError(
                "WEB_UNSUPPORTED_CONTENT_TYPE",
                "web response content type is not textual",
                details={"content_type": content_type[:256]},
            )

        content = bytearray()
        async for chunk in response.aiter_content(chunk_size=8192):
            content.extend(chunk)
            if len(content) > self.max_response_bytes:
                raise WebToolError(
                    "WEB_RESPONSE_TOO_LARGE",
                    "web response exceeds the hard byte limit",
                    details={"max_bytes": self.max_response_bytes},
                )

        encoding = getattr(response, "encoding", None)
        if not isinstance(encoding, str) or not encoding:
            encoding = "utf-8"
        return content.decode(encoding, errors="replace")

    async def fetch_static(
        self,
        *,
        url: str,
        deadline: float,
        profile: str,
        proxy: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        if self._session_factory is None:
            raise dependency_error("curl_cffi")

        await self._await_deadline(
            self._request_semaphore.acquire(),
            deadline,
            stage="request_slot",
        )
        try:
            target = await self._await_deadline(
                self._policy.resolve_url(url),
                deadline,
                stage="static_dns",
            )
            redirect_chain: list[dict[str, Any]] = []
            proxy_used = bool(proxy)

            for redirect_index in range(MAX_REDIRECTS + 1):
                session_kwargs: dict[str, Any] = {
                    "impersonate": profile,
                    "trust_env": False,
                }
                proxy_target: ResolvedTarget | None = None
                if proxy:
                    proxy_url = (
                        proxy.get(target.scheme)
                        or proxy.get("https")
                        or proxy.get("http")
                    )
                    if not isinstance(proxy_url, str) or not proxy_url:
                        raise WebToolError(
                            "INVALID_ARGUMENT",
                            "proxy mapping does not contain a usable endpoint",
                        )
                    proxy_target = await self._await_deadline(
                        self._policy.resolve_proxy(proxy_url),
                        deadline,
                        stage="proxy_dns",
                    )
                    session_kwargs["proxies"] = proxy
                    session_kwargs["curl_options"] = self._curl_options(
                        proxy_target
                    )
                else:
                    session_kwargs["curl_options"] = self._curl_options(target)

                try:
                    async with self._session_factory(**session_kwargs) as session:
                        response = await self._await_deadline(
                            session.get(
                                target.url,
                                headers={
                                "Accept": (
                                    "text/html,application/xhtml+xml,application/xml,"
                                    "application/json,text/plain;q=0.9,*/*;q=0.1"
                                ),
                                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
                                "User-Agent": (
                                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/122.0.0.0 Safari/537.36"
                                ),
                            },
                                timeout=remaining_timeout(deadline),
                                stream=True,
                                allow_redirects=False,
                            ),
                            deadline,
                            stage="static_request",
                        )

                        status = int(response.status_code)
                        self._policy.verify_primary_ip(
                            getattr(response, "primary_ip", None),
                            proxy_target if proxy_target is not None else target,
                        )

                        if status in _REDIRECT_HTTP:
                            if redirect_index >= MAX_REDIRECTS:
                                raise WebToolError(
                                    "WEB_REDIRECT_LIMIT",
                                    "web redirect limit exceeded",
                                    details={"max_redirects": MAX_REDIRECTS},
                                )
                            location = (getattr(response, "headers", {}) or {}).get(
                                "Location"
                            )
                            redirect_chain.append(
                                {"url": target.url, "status_code": status}
                            )
                            target = await self._await_deadline(
                                self._policy.resolve_redirect(
                                    target.url,
                                    location,
                                ),
                                deadline,
                                stage="redirect_dns",
                            )
                            continue

                        if status >= 400:
                            raise WebToolError(
                                "WEB_HTTP_ERROR",
                                "web server returned an unsuccessful HTTP status",
                                retryable=status in _RETRYABLE_HTTP,
                                details={
                                    "status_code": status,
                                    "retry_after_seconds": retry_after_seconds(
                                        getattr(response, "headers", {}) or {}
                                    ),
                                },
                            )

                        html = await self._await_deadline(
                            self._read_bounded_response(response),
                            deadline,
                            stage="response_body",
                        )
                        headers = getattr(response, "headers", {}) or {}
                        return {
                            "html": html,
                            "status_code": status,
                            "requested_url": url,
                            "final_url": target.url,
                            "redirect_chain": redirect_chain,
                            "content_type": str(
                                headers.get("Content-Type", "")
                            )[:256],
                            "proxy_used": proxy_used,
                            "rendered": False,
                            "wait_selector_satisfied": None,
                            "structured_data": None,
                        }
                except asyncio.CancelledError:
                    raise
                except WebToolError:
                    raise
                except Exception as exc:
                    raise WebToolError(
                        "WEB_NETWORK_ERROR",
                        "static web request failed",
                        retryable=True,
                        details={"exception_type": type(exc).__name__},
                    ) from exc

            raise WebToolError(
                "WEB_REDIRECT_LIMIT",
                "web redirect limit exceeded",
                details={"max_redirects": MAX_REDIRECTS},
            )
        finally:
            self._request_semaphore.release()

    async def _browser_redirect_chain(self, response: Any) -> list[dict[str, Any]]:
        chain: list[dict[str, Any]] = []
        try:
            request = response.request
            previous = request.redirected_from
            items: list[Any] = []
            while previous is not None:
                items.append(previous)
                if len(items) > MAX_REDIRECTS:
                    raise WebToolError(
                        "WEB_REDIRECT_LIMIT",
                        "browser redirect limit exceeded",
                        details={"max_redirects": MAX_REDIRECTS},
                    )
                previous = previous.redirected_from
            for item in reversed(items):
                chain.append(
                    {
                        "url": str(item.url),
                        "status_code": None,
                    }
                )
        except WebToolError:
            raise
        except Exception:
            return []
        return chain

    async def fetch_dynamic(
        self,
        *,
        url: str,
        deadline: float,
        wait_selector: Optional[str] = None,
        proxy: Optional[str] = None,
    ) -> dict[str, Any]:
        await self._await_deadline(
            self._policy.resolve_url(url),
            deadline,
            stage="browser_dns",
        )

        await self._await_deadline(
            self._request_semaphore.acquire(),
            deadline,
            stage="request_slot",
        )
        try:
            await self._await_deadline(
                self._browser_semaphore.acquire(),
                deadline,
                stage="browser_slot",
            )
            try:
                browser = await self._await_deadline(
                    self._get_browser(),
                    deadline,
                    stage="browser_start",
                )
                context = None
                operation_error: BaseException | None = None
                result: dict[str, Any] | None = None
                cleanup_error: Exception | None = None
                blocked_error: WebToolError | None = None

                try:
                    proxy_target: ResolvedTarget | None = None
                    context_options: dict[str, Any] = {
                        "user_agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/122.0.0.0 Safari/537.36"
                        ),
                        "viewport": {"width": 1920, "height": 1080},
                        "locale": "vi-VN",
                        "timezone_id": "Asia/Ho_Chi_Minh",
                        "service_workers": "block",
                        "accept_downloads": False,
                    }
                    if proxy:
                        proxy_target = await self._await_deadline(
                            self._policy.resolve_proxy(proxy),
                            deadline,
                            stage="browser_proxy_dns",
                        )
                        context_options["proxy"] = self._playwright_proxy_settings(
                            proxy,
                            resolved_target=proxy_target,
                        )

                    context = await self._await_deadline(
                        browser.new_context(**context_options),
                        deadline,
                        stage="browser_context",
                    )

                    async def route_handler(route: Any) -> None:
                        nonlocal blocked_error
                        request = route.request
                        request_url = str(request.url)
                        scheme = urlsplit(request_url).scheme.lower()
                        if scheme in _ALLOWED_BROWSER_LOCAL_SCHEMES:
                            await route.continue_()
                            return
                        if scheme not in {"http", "https"}:
                            blocked_error = WebToolError(
                                "WEB_BROWSER_REQUEST_BLOCKED",
                                "browser request scheme is not allowed",
                            )
                            await route.abort()
                            return
                        try:
                            await self._await_deadline(
                                self._policy.resolve_url(request_url),
                                deadline,
                                stage="browser_subrequest_dns",
                            )
                        except WebToolError as exc:
                            if exc.code == "WEB_TIMEOUT":
                                blocked_error = exc
                            else:
                                blocked_error = WebToolError(
                                    "WEB_BROWSER_REQUEST_BLOCKED",
                                    "browser request was blocked by network policy",
                                    details={"reason": exc.code},
                                )
                            await route.abort()
                            return
                        resource_type = getattr(request, "resource_type", "")
                        if resource_type in _BLOCKED_RESOURCE_TYPES:
                            await route.abort()
                            return
                        await route.continue_()

                    await self._await_deadline(
                        context.route("**/*", route_handler),
                        deadline,
                        stage="browser_route_install",
                    )

                    route_web_socket = getattr(
                        context,
                        "route_web_socket",
                        None,
                    )
                    if not callable(route_web_socket):
                        raise WebToolError(
                            "DEPENDENCY_UNAVAILABLE",
                            "Playwright WebSocket routing is required by the Web safety contract",
                            details={"dependency": "playwright.route_web_socket"},
                        )

                    async def ws_handler(web_socket_route: Any) -> None:
                        close = getattr(web_socket_route, "close", None)
                        if close is None:
                            raise WebToolError(
                                "WEB_BROWSER_REQUEST_BLOCKED",
                                "WebSocket route could not be closed safely",
                            )
                        maybe = close()
                        if asyncio.iscoroutine(maybe):
                            await maybe

                    await self._await_deadline(
                        route_web_socket("**/*", ws_handler),
                        deadline,
                        stage="browser_websocket_route_install",
                    )

                    page = await self._await_deadline(
                        context.new_page(),
                        deadline,
                        stage="browser_page_create",
                    )
                    await self._await_deadline(
                        WebToolStealth.apply_stealth_scripts(page),
                        deadline,
                        stage="browser_init_script",
                    )

                    try:
                        response = await self._await_deadline(
                            page.goto(
                                url,
                                timeout=max(
                                    1,
                                    int(remaining_timeout(deadline) * 1000),
                                ),
                                wait_until="domcontentloaded",
                            ),
                            deadline,
                            stage="navigation",
                        )
                    except asyncio.CancelledError:
                        raise
                    except WebToolError:
                        if blocked_error is not None:
                            raise blocked_error
                        raise
                    except Exception as exc:
                        if blocked_error is not None:
                            raise blocked_error
                        raise WebToolError(
                            "WEB_NETWORK_ERROR",
                            "browser navigation failed",
                            retryable=True,
                            details={"exception_type": type(exc).__name__},
                        ) from exc

                    if blocked_error is not None:
                        raise blocked_error
                    if response is None:
                        raise WebToolError(
                            "WEB_NETWORK_ERROR",
                            "browser navigation produced no response",
                            retryable=True,
                        )

                    status = int(response.status)
                    headers = getattr(response, "headers", {}) or {}
                    if status >= 400:
                        raise WebToolError(
                            "WEB_HTTP_ERROR",
                            "browser navigation returned an unsuccessful HTTP status",
                            retryable=status in _RETRYABLE_HTTP,
                            details={
                                "status_code": status,
                                "retry_after_seconds": retry_after_seconds(headers),
                            },
                        )

                    content_type = str(
                        headers.get("content-type")
                        or headers.get("Content-Type")
                        or ""
                    )
                    if content_type and not self._content_type_allowed(content_type):
                        raise WebToolError(
                            "WEB_UNSUPPORTED_CONTENT_TYPE",
                            "browser response content type is not textual",
                            details={"content_type": content_type[:256]},
                        )

                    declared = (
                        headers.get("content-length")
                        or headers.get("Content-Length")
                    )
                    if declared:
                        try:
                            if int(declared) > self.max_response_bytes:
                                raise WebToolError(
                                    "WEB_RESPONSE_TOO_LARGE",
                                    "browser response exceeds the hard byte limit",
                                    details={"max_bytes": self.max_response_bytes},
                                )
                        except ValueError:
                            pass

                    redirect_chain = await self._await_deadline(
                        self._browser_redirect_chain(response),
                        deadline,
                        stage="browser_redirect_chain",
                    )
                    final_url = str(page.url)
                    await self._await_deadline(
                        self._policy.resolve_url(final_url),
                        deadline,
                        stage="browser_final_dns",
                    )

                    selector_satisfied: bool | None = None
                    if wait_selector is not None:
                        try:
                            await self._await_deadline(
                                page.wait_for_selector(
                                    wait_selector,
                                    timeout=max(
                                        1,
                                        int(remaining_timeout(deadline) * 1000),
                                    ),
                                    state="visible",
                                ),
                                deadline,
                                stage="wait_selector",
                            )
                            selector_satisfied = True
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            raise WebToolError(
                                "WEB_SELECTOR_TIMEOUT",
                                "requested selector did not become visible before timeout",
                                details={"exception_type": type(exc).__name__},
                            ) from exc

                    if blocked_error is not None:
                        raise blocked_error

                    html = await self._await_deadline(
                        page.content(),
                        deadline,
                        stage="page_content",
                    )
                    if blocked_error is not None:
                        raise blocked_error
                    if len(html) > MAX_RENDERED_HTML_CHARS:
                        raise WebToolError(
                            "WEB_RESPONSE_TOO_LARGE",
                            "rendered HTML exceeds the hard character limit",
                            details={"max_chars": MAX_RENDERED_HTML_CHARS},
                        )
                    if WebToolStealth.is_captcha_or_cf_present(html):
                        raise WebToolError(
                            "WEB_CHALLENGE_REQUIRED",
                            "page requires an interactive anti-bot challenge",
                        )

                    structured = await self._await_deadline(
                        extract_tables_and_charts(html, page_obj=page),
                        deadline,
                        stage="structured_extraction",
                    )
                    title = str(
                        await self._await_deadline(
                            page.title(),
                            deadline,
                            stage="page_title",
                        )
                    )
                    if blocked_error is not None:
                        raise blocked_error
                    result = {
                        "html": html,
                        "status_code": status,
                        "requested_url": url,
                        "final_url": final_url,
                        "redirect_chain": redirect_chain,
                        "content_type": content_type[:256],
                        "proxy_used": bool(proxy),
                        "rendered": True,
                        "wait_selector_satisfied": selector_satisfied,
                        "structured_data": structured,
                        "browser_title": title,
                    }
                except BaseException as exc:
                    operation_error = exc
                finally:
                    if context is not None:
                        try:
                            await context.close()
                        except Exception as exc:
                            cleanup_error = exc

                if operation_error is not None:
                    if isinstance(
                        operation_error,
                        (asyncio.CancelledError, KeyboardInterrupt, SystemExit),
                    ):
                        raise operation_error

                    if isinstance(operation_error, WebToolError):
                        normalized_error = operation_error
                    else:
                        normalized_error = WebToolError(
                            "WEB_NETWORK_ERROR",
                            "browser backend operation failed",
                            retryable=True,
                            details={
                                "exception_type": type(operation_error).__name__,
                            },
                        )

                    if cleanup_error is not None:
                        raise WebToolError(
                            "WEB_CLEANUP_FAILED",
                            "browser context could not be closed after a failed operation",
                            details={"exception_type": type(cleanup_error).__name__},
                        ) from cleanup_error
                    raise normalized_error from operation_error

                if cleanup_error is not None:
                    raise WebToolError(
                        "WEB_CLEANUP_FAILED",
                        "browser context could not be closed cleanly",
                        details={"exception_type": type(cleanup_error).__name__},
                    ) from cleanup_error
                assert result is not None
                return result
            finally:
                self._browser_semaphore.release()
        finally:
            self._request_semaphore.release()
