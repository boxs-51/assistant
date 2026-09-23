from __future__ import annotations

import asyncio
import ipaddress
import time
from typing import Any

from bs4 import BeautifulSoup

try:
    from curl_cffi import CurlOpt
    from curl_cffi.requests import AsyncSession
except ImportError:
    CurlOpt = None
    AsyncSession = None

from .config import (
    MAX_SEARCH_RESPONSE_BYTES,
    MAX_SEARCH_SNIPPET_CHARS,
    MAX_SEARCH_TITLE_CHARS,
    MAX_URL_CHARS,
)
from .errors import WebToolError, dependency_error
from .network_policy import NetworkPolicy, ResolvedTarget
from .utils import clean_whitespace, public_result_url


def _remaining_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise WebToolError(
            "WEB_TIMEOUT",
            "web search exceeded its total timeout",
            retryable=True,
        )
    return remaining


async def _await_deadline(
    awaitable: Any,
    deadline: float,
    *,
    stage: str,
) -> Any:
    try:
        timeout = _remaining_timeout(deadline)
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
            "web search exceeded its total timeout",
            retryable=True,
            details={"stage": stage},
        ) from exc


class WebSearcher:
    PROVIDER = "duckduckgo_html"
    ENDPOINT = "https://html.duckduckgo.com/html/"

    def __init__(
        self,
        *,
        network_policy: NetworkPolicy,
        session_factory: Any = None,
    ) -> None:
        self._policy = network_policy
        self._session_factory = session_factory or AsyncSession

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

    async def search(
        self,
        *,
        query: str,
        max_results: int,
        timeout: float,
    ) -> list[dict[str, str]]:
        if self._session_factory is None:
            raise dependency_error("curl_cffi")

        deadline = time.monotonic() + timeout
        target = await _await_deadline(
            self._policy.resolve_url(self.ENDPOINT),
            deadline,
            stage="search_dns",
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://html.duckduckgo.com/",
        }

        try:
            async with self._session_factory(
                impersonate="chrome120",
                trust_env=False,
                curl_options=self._curl_options(target),
            ) as session:
                response = await _await_deadline(
                    session.post(
                        target.url,
                        data={"q": query},
                        headers=headers,
                        timeout=_remaining_timeout(deadline),
                        allow_redirects=False,
                        stream=True,
                    ),
                    deadline,
                    stage="search_request",
                )
                if response.status_code != 200:
                    raise WebToolError(
                        "WEB_SEARCH_PROVIDER_FAILED",
                        "search provider returned an unsuccessful HTTP status",
                        retryable=response.status_code in {408, 425, 429, 500, 502, 503, 504},
                        details={"status_code": int(response.status_code)},
                    )

                self._policy.verify_primary_ip(
                    getattr(response, "primary_ip", None),
                    target,
                )

                content_type = str(
                    response.headers.get("Content-Type")
                    or response.headers.get("content-type")
                    or ""
                ).lower()
                if content_type and "html" not in content_type:
                    raise WebToolError(
                        "WEB_SEARCH_PROVIDER_FAILED",
                        "search provider returned an unexpected content type",
                        details={"content_type": content_type[:256]},
                    )

                declared = (
                    response.headers.get("Content-Length")
                    or response.headers.get("content-length")
                )
                if declared:
                    try:
                        if int(declared) > MAX_SEARCH_RESPONSE_BYTES:
                            raise WebToolError(
                                "WEB_SEARCH_PROVIDER_FAILED",
                                "search provider response exceeded its hard limit",
                            )
                    except ValueError:
                        pass

                async def read_body() -> bytearray:
                    body = bytearray()
                    async for chunk in response.aiter_content(chunk_size=8192):
                        body.extend(chunk)
                        if len(body) > MAX_SEARCH_RESPONSE_BYTES:
                            raise WebToolError(
                                "WEB_SEARCH_PROVIDER_FAILED",
                                "search provider response exceeded its hard limit",
                            )
                    return body

                body = await _await_deadline(
                    read_body(),
                    deadline,
                    stage="search_response_body",
                )
        except asyncio.CancelledError:
            raise
        except WebToolError:
            raise
        except Exception as exc:
            raise WebToolError(
                "WEB_SEARCH_PROVIDER_FAILED",
                "search provider request failed",
                retryable=True,
                details={"exception_type": type(exc).__name__},
            ) from exc

        encoding = getattr(response, "encoding", None) or "utf-8"
        html = body.decode(encoding, errors="replace")
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:
            raise WebToolError(
                "WEB_SEARCH_PROVIDER_FAILED",
                "search provider response could not be parsed",
                details={"exception_type": type(exc).__name__},
            ) from exc

        result_nodes = soup.select(".result:not(.result--ad)")
        if not result_nodes:
            valid_empty = (
                soup.select_one(".no-results__message") is not None
                or soup.select_one(".result--no-result") is not None
                or "no results" in clean_whitespace(
                    soup.get_text(" ", strip=True)
                ).lower()
            )
            if not valid_empty:
                raise WebToolError(
                    "WEB_SEARCH_PROVIDER_FAILED",
                    "search provider response lacked expected result markers",
                )
            return []

        results: list[dict[str, str]] = []
        for result_div in result_nodes:
            anchor = result_div.select_one("a.result__a")
            if anchor is None:
                continue
            title = clean_whitespace(anchor.get_text(" ", strip=True))[:MAX_SEARCH_TITLE_CHARS]
            url = public_result_url(anchor.get("href", ""), MAX_URL_CHARS)
            snippet_node = result_div.select_one(".result__snippet")
            snippet = (
                clean_whitespace(snippet_node.get_text(" ", strip=True))[:MAX_SEARCH_SNIPPET_CHARS]
                if snippet_node is not None
                else ""
            )
            if not title or not url:
                continue
            results.append({"title": title, "url": url, "snippet": snippet})
            if len(results) >= max_results:
                break
        return results
