from __future__ import annotations

import asyncio
import hashlib
import math
import random
import time
from datetime import datetime, timezone
from typing import Any, Optional

from bs4 import BeautifulSoup

from tools.v1._shared.contracts import failure_result, success_result

from .config import (
    DEFAULT_IMPERSONATE_PROFILES,
    MAX_BATCH_URLS,
    MAX_CONTENT_CHARS_DEFAULT,
    MAX_CONTENT_CHARS_MAX,
    MAX_CONTENT_CHARS_MIN,
    MAX_QUERY_CHARS,
    MAX_RETRIES,
    MAX_STATIC_RESPONSE_BYTES,
    MAX_TITLE_CHARS,
    MAX_WAIT_SELECTOR_CHARS,
    READ_TIMEOUT_DEFAULT,
    READ_TIMEOUT_MAX,
    READ_TIMEOUT_MIN,
    SEARCH_RESULTS_DEFAULT,
    SEARCH_RESULTS_MAX,
    SEARCH_RESULTS_MIN,
    WEB_CONCURRENCY_DEFAULT,
    WEB_CONCURRENCY_MAX,
    WEB_CONCURRENCY_MIN,
    WEB_TOOL_NAME,
    WEB_TOOL_VERSION,
)
from .errors import WebToolError
from .extractors import extract_clean_content, extract_tables_and_charts
from .network_policy import NetworkPolicy
from .proxy import ProxyManager
from .scraper import WebScraper, remaining_timeout
from .searcher import WebSearcher
from .utils import clean_whitespace


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


def _validate_bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise WebToolError("INVALID_ARGUMENT", f"{name} must be a boolean")
    return value


def _validate_int(
    value: Any,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int:
        raise WebToolError("INVALID_ARGUMENT", f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise WebToolError(
            "INVALID_ARGUMENT",
            f"{name} must be within [{minimum}, {maximum}]",
        )
    return value


def _validate_float(
    value: Any,
    *,
    name: str,
    minimum: float,
    maximum: float,
) -> float:
    if type(value) not in {int, float}:
        raise WebToolError("INVALID_ARGUMENT", f"{name} must be a number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < minimum or numeric > maximum:
        raise WebToolError(
            "INVALID_ARGUMENT",
            f"{name} must be finite and within [{minimum}, {maximum}]",
        )
    return numeric


def _validate_string(
    value: Any,
    *,
    name: str,
    maximum: int,
    allow_none: bool = False,
) -> Optional[str]:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise WebToolError("INVALID_ARGUMENT", f"{name} must be a non-empty string")
    if len(value) > maximum:
        raise WebToolError(
            "INVALID_ARGUMENT",
            f"{name} exceeds maximum length {maximum}",
        )
    return value.strip()


def _validate_output_format(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise WebToolError("INVALID_ARGUMENT", "output_format must be a string")
    if value.lower() != "markdown":
        raise WebToolError(
            "INVALID_ARGUMENT",
            "T6 returns structured ToolResult; only legacy output_format='markdown' is accepted",
        )


class WebTool:
    def __init__(
        self,
        proxy_list: Optional[list[str]] = None,
        impersonate_profiles: Optional[list[str]] = None,
        default_timeout: float = READ_TIMEOUT_DEFAULT,
        max_response_bytes: int = MAX_STATIC_RESPONSE_BYTES,
        min_content_length: int = 200,
        default_max_chars: int = MAX_CONTENT_CHARS_DEFAULT,
        captcha_api_key: Optional[str] = None,
        max_concurrency: int = WEB_CONCURRENCY_DEFAULT,
        *,
        network_policy: Optional[NetworkPolicy] = None,
        session_factory: Any = None,
        playwright_factory: Any = None,
    ) -> None:
        self.default_timeout = _validate_float(
            default_timeout,
            name="default_timeout",
            minimum=READ_TIMEOUT_MIN,
            maximum=READ_TIMEOUT_MAX,
        )
        self.default_max_chars = _validate_int(
            default_max_chars,
            name="default_max_chars",
            minimum=MAX_CONTENT_CHARS_MIN,
            maximum=MAX_CONTENT_CHARS_MAX,
        )
        self.max_response_bytes = _validate_int(
            max_response_bytes,
            name="max_response_bytes",
            minimum=1,
            maximum=MAX_STATIC_RESPONSE_BYTES,
        )
        self.max_concurrency = _validate_int(
            max_concurrency,
            name="max_concurrency",
            minimum=WEB_CONCURRENCY_MIN,
            maximum=WEB_CONCURRENCY_MAX,
        )
        self.min_content_length = _validate_int(
            min_content_length,
            name="min_content_length",
            minimum=0,
            maximum=MAX_CONTENT_CHARS_MAX,
        )
        if captcha_api_key is not None:
            raise WebToolError(
                "INVALID_ARGUMENT",
                "automatic CAPTCHA solving is not part of T6 ordinary web read",
            )

        if impersonate_profiles is None:
            self.profiles = list(DEFAULT_IMPERSONATE_PROFILES)
        else:
            if (
                not isinstance(impersonate_profiles, list)
                or not impersonate_profiles
                or len(impersonate_profiles) > 16
            ):
                raise WebToolError(
                    "INVALID_ARGUMENT",
                    "impersonate_profiles must be a non-empty bounded list",
                )
            profiles: list[str] = []
            for index, profile in enumerate(impersonate_profiles):
                if not isinstance(profile, str) or not profile.strip() or len(profile) > 128:
                    raise WebToolError(
                        "INVALID_ARGUMENT",
                        f"impersonate_profiles[{index}] is invalid",
                    )
                profiles.append(profile.strip())
            self.profiles = profiles

        self.network_policy = network_policy or NetworkPolicy()
        self.proxy_manager = ProxyManager(
            proxy_list,
            network_policy=self.network_policy,
            session_factory=session_factory,
        )
        self.searcher = WebSearcher(
            network_policy=self.network_policy,
            session_factory=session_factory,
        )
        self.scraper = WebScraper(
            network_policy=self.network_policy,
            max_response_bytes=self.max_response_bytes,
            max_concurrency=self.max_concurrency,
            session_factory=session_factory,
            playwright_factory=playwright_factory,
        )

    async def __aenter__(self) -> "WebTool":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None and not issubclass(exc_type, WebToolError):
            try:
                await self.close()
            except Exception:
                pass
            return None
        await self.close()
        return None

    async def close(self) -> None:
        await self.scraper.close()

    def _success(
        self,
        action: str,
        data: Any,
        *,
        truncated: bool = False,
        warnings: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        return success_result(
            tool=WEB_TOOL_NAME,
            action=action,
            version=WEB_TOOL_VERSION,
            data=data,
            truncated=truncated,
            warnings=warnings,
        )

    def _failure(self, action: str, error: WebToolError) -> dict[str, Any]:
        return failure_result(
            tool=WEB_TOOL_NAME,
            action=action,
            version=WEB_TOOL_VERSION,
            code=error.code,
            message=error.message,
            retryable=error.retryable,
            details=error.details,
        )

    @staticmethod
    def _validate_legacy_options(
        *,
        output_format: Any,
        captcha_api_key: Any,
    ) -> None:
        _validate_output_format(output_format)
        if captcha_api_key is not None:
            raise WebToolError(
                "INVALID_ARGUMENT",
                "captcha_api_key is not accepted by T6 ordinary web read",
            )

    async def search(
        self,
        query: str,
        max_results: int = SEARCH_RESULTS_DEFAULT,
        output_format: str = "markdown",
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        action = "search"
        try:
            _validate_output_format(output_format)
            clean_query = _validate_string(
                query,
                name="query",
                maximum=MAX_QUERY_CHARS,
            )
            assert clean_query is not None
            result_limit = _validate_int(
                max_results,
                name="max_results",
                minimum=SEARCH_RESULTS_MIN,
                maximum=SEARCH_RESULTS_MAX,
            )
            timeout_seconds = (
                self.default_timeout
                if timeout is None
                else _validate_float(
                    timeout,
                    name="timeout",
                    minimum=READ_TIMEOUT_MIN,
                    maximum=READ_TIMEOUT_MAX,
                )
            )
            results = await self.searcher.search(
                query=clean_query,
                max_results=result_limit,
                timeout=timeout_seconds,
            )
            return self._success(
                action,
                {
                    "query": clean_query,
                    "returned_count": len(results),
                    "provider": self.searcher.PROVIDER,
                    "results": results,
                },
            )
        except asyncio.CancelledError:
            raise
        except WebToolError as exc:
            return self._failure(action, exc)

    async def _refresh_proxy_if_needed(
        self,
        deadline: float,
    ) -> Optional[dict[str, str]]:
        if not self.proxy_manager.raw_proxy_list:
            return None
        if not self.proxy_manager.valid_proxies:
            count = await _await_deadline(
                self.proxy_manager.refresh_proxy_pool(
                    timeout=min(3.0, remaining_timeout(deadline))
                ),
                deadline,
                stage="proxy_refresh",
            )
            if count == 0:
                raise WebToolError(
                    "WEB_PROXY_UNAVAILABLE",
                    "no configured proxy passed validation",
                    retryable=True,
                )
        selected = self.proxy_manager.get_fastest_proxy()
        if selected is None:
            raise WebToolError(
                "WEB_PROXY_UNAVAILABLE",
                "no validated proxy is available",
                retryable=True,
            )
        await _await_deadline(
            self.proxy_manager.validate_proxy(selected["http"]),
            deadline,
            stage="proxy_dns",
        )
        return selected

    async def _static_with_retries(
        self,
        *,
        url: str,
        deadline: float,
        max_retries: int,
        proxy: Optional[dict[str, str]],
    ) -> dict[str, Any]:
        current_proxy = proxy
        last_error: WebToolError | None = None

        for attempt in range(1, max_retries + 1):
            try:
                profile = self.profiles[(attempt - 1) % len(self.profiles)]
                return await self.scraper.fetch_static(
                    url=url,
                    deadline=deadline,
                    profile=profile,
                    proxy=current_proxy,
                )
            except asyncio.CancelledError:
                raise
            except WebToolError as exc:
                last_error = exc
                status_code = exc.details.get("status_code")
                if (
                    exc.code == "WEB_HTTP_ERROR"
                    and status_code == 403
                    and current_proxy is not None
                ):
                    failed_proxy = current_proxy.get("http")
                    self.proxy_manager.remove_proxy(failed_proxy)
                    current_proxy = self.proxy_manager.get_fastest_proxy()
                    if current_proxy is None:
                        raise WebToolError(
                            "WEB_PROXY_UNAVAILABLE",
                            "all configured proxies were rejected",
                            retryable=True,
                        ) from exc
                    continue

                if not exc.retryable or attempt >= max_retries:
                    raise

                retry_after = exc.details.get("retry_after_seconds")
                if type(retry_after) not in {int, float} or retry_after is None:
                    retry_after = min(0.2 * attempt, 1.0)
                sleep_for = min(float(retry_after), remaining_timeout(deadline))
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)

        assert last_error is not None
        raise last_error

    async def _extract_content(
        self,
        *,
        html: str,
        base_url: str,
        clean_noise: bool,
        deduplicate: bool,
        deadline: float,
    ) -> tuple[Optional[str], str]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    extract_clean_content,
                    html,
                    base_url,
                    clean_noise,
                    deduplicate,
                ),
                timeout=remaining_timeout(deadline),
            )
        except asyncio.TimeoutError as exc:
            raise WebToolError(
                "WEB_TIMEOUT",
                "content extraction exceeded the web operation deadline",
                retryable=True,
            ) from exc
        except WebToolError:
            raise
        except Exception as exc:
            raise WebToolError(
                "WEB_EXTRACTION_FAILED",
                "content extraction failed",
                details={"exception_type": type(exc).__name__},
            ) from exc

    @staticmethod
    def _extract_title(html: str) -> tuple[str, bool]:
        try:
            soup = BeautifulSoup(html, "html.parser")
            raw = (
                clean_whitespace(soup.title.string)
                if soup.title is not None and soup.title.string
                else ""
            )
        except Exception:
            raw = ""
        if not raw:
            raw = "Untitled"
        return raw[:MAX_TITLE_CHARS], len(raw) > MAX_TITLE_CHARS

    async def scrape(
        self,
        url: str,
        force_js: bool = False,
        wait_selector: Optional[str] = None,
        timeout: Optional[float] = None,
        max_chars: Optional[int] = None,
        captcha_api_key: Optional[str] = None,
        output_format: str = "markdown",
        max_retries: int = MAX_RETRIES,
        clean_noise: bool = True,
        deduplicate: bool = True,
    ) -> dict[str, Any]:
        action = "scrape"
        try:
            self._validate_legacy_options(
                output_format=output_format,
                captcha_api_key=captcha_api_key,
            )
            _validate_bool(force_js, "force_js")
            _validate_bool(clean_noise, "clean_noise")
            _validate_bool(deduplicate, "deduplicate")
            selector = (
                None
                if wait_selector is None
                else _validate_string(
                    wait_selector,
                    name="wait_selector",
                    maximum=MAX_WAIT_SELECTOR_CHARS,
                )
            )
            timeout_seconds = (
                self.default_timeout
                if timeout is None
                else _validate_float(
                    timeout,
                    name="timeout",
                    minimum=READ_TIMEOUT_MIN,
                    maximum=READ_TIMEOUT_MAX,
                )
            )
            char_limit = (
                self.default_max_chars
                if max_chars is None
                else _validate_int(
                    max_chars,
                    name="max_chars",
                    minimum=MAX_CONTENT_CHARS_MIN,
                    maximum=MAX_CONTENT_CHARS_MAX,
                )
            )
            retries = _validate_int(
                max_retries,
                name="max_retries",
                minimum=1,
                maximum=MAX_RETRIES,
            )

            deadline = time.monotonic() + timeout_seconds
            target = await _await_deadline(
                self.network_policy.resolve_url(url),
                deadline,
                stage="initial_dns",
            )
            requested_url = target.url
            proxy = await self._refresh_proxy_if_needed(deadline)
            proxy_string = proxy.get("http") if proxy is not None else None

            source: dict[str, Any] | None = None
            static_content: str | None = None
            static_extractor = "none"
            static_error: WebToolError | None = None
            warnings: list[str] = []

            if not force_js:
                try:
                    source = await self._static_with_retries(
                        url=requested_url,
                        deadline=deadline,
                        max_retries=retries,
                        proxy=proxy,
                    )
                    static_content, static_extractor = await self._extract_content(
                        html=source["html"],
                        base_url=source["final_url"],
                        clean_noise=clean_noise,
                        deduplicate=deduplicate,
                        deadline=deadline,
                    )
                except asyncio.CancelledError:
                    raise
                except WebToolError as exc:
                    if source is not None and exc.code == "WEB_EXTRACTION_FAILED":
                        raise
                    static_error = exc

            need_dynamic = (
                force_js
                or selector is not None
                or source is None
                or not static_content
                or len(static_content) < self.min_content_length
            )

            dynamic_error: WebToolError | None = None
            if need_dynamic:
                allow_dynamic = force_js or selector is not None or source is not None
                if source is None and static_error is not None:
                    status = static_error.details.get("status_code")
                    allow_dynamic = (
                        static_error.code in {"DEPENDENCY_UNAVAILABLE", "WEB_NETWORK_ERROR"}
                        or (
                            static_error.code == "WEB_HTTP_ERROR"
                            and status in {403, 429, 503}
                        )
                    )
                if allow_dynamic:
                    try:
                        dynamic_source = await self.scraper.fetch_dynamic(
                            url=requested_url,
                            deadline=deadline,
                            wait_selector=selector,
                            proxy=proxy_string,
                        )
                        dynamic_content, dynamic_extractor = await self._extract_content(
                            html=dynamic_source["html"],
                            base_url=dynamic_source["final_url"],
                            clean_noise=clean_noise,
                            deduplicate=deduplicate,
                            deadline=deadline,
                        )
                        if dynamic_content:
                            source = dynamic_source
                            static_content = dynamic_content
                            static_extractor = dynamic_extractor
                    except asyncio.CancelledError:
                        raise
                    except WebToolError as exc:
                        dynamic_error = exc

            if source is None:
                if dynamic_error is not None:
                    raise dynamic_error
                if static_error is not None:
                    raise static_error
                raise WebToolError(
                    "WEB_EXTRACTION_FAILED",
                    "no web source could be fetched",
                )

            content = static_content
            if not content:
                if dynamic_error is not None:
                    raise dynamic_error
                raise WebToolError(
                    "WEB_EXTRACTION_FAILED",
                    "web page content could not be extracted",
                )

            if dynamic_error is not None:
                must_surface = (
                    force_js
                    or selector is not None
                    or dynamic_error.code
                    in {
                        "WEB_CHALLENGE_REQUIRED",
                        "WEB_BROWSER_REQUEST_BLOCKED",
                        "WEB_URL_BLOCKED",
                        "WEB_REDIRECT_BLOCKED",
                        "WEB_REDIRECT_LIMIT",
                        "WEB_RESPONSE_TOO_LARGE",
                        "WEB_STRUCTURED_DATA_LIMIT",
                        "WEB_CLEANUP_FAILED",
                    }
                )
                if must_surface:
                    raise dynamic_error
                if need_dynamic:
                    warnings.append(
                        "dynamic fallback unavailable; returning bounded static content"
                    )

            structured_data = source.get("structured_data")
            if structured_data is None:
                structured_data = await _await_deadline(
                    extract_tables_and_charts(source["html"]),
                    deadline,
                    stage="structured_extraction",
                )

            title_source = source.get("browser_title")
            if isinstance(title_source, str) and title_source.strip():
                raw_title = clean_whitespace(title_source)
                title = raw_title[:MAX_TITLE_CHARS]
                title_truncated = len(raw_title) > MAX_TITLE_CHARS
            else:
                title, title_truncated = self._extract_title(source["html"])

            full_content = content
            digest = hashlib.sha256(full_content.encode("utf-8")).hexdigest()
            truncated = len(full_content) > char_limit
            returned_content = full_content[:char_limit]

            data = {
                "requested_url": requested_url,
                "final_url": source["final_url"],
                "status_code": int(source["status_code"]),
                "title": title,
                "title_truncated": title_truncated,
                "content": returned_content,
                "content_format": "markdown",
                "content_chars": len(full_content),
                "returned_chars": len(returned_content),
                "content_sha256": digest,
                "method": "dynamic" if source.get("rendered") else "static",
                "content_type": source.get("content_type", ""),
                "structured_data": structured_data,
                "provenance": {
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "redirect_chain": source.get("redirect_chain", []),
                    "extractor": static_extractor,
                    "rendered": bool(source.get("rendered")),
                    "proxy_used": bool(source.get("proxy_used")),
                    "wait_selector_satisfied": source.get(
                        "wait_selector_satisfied"
                    ),
                },
            }
            return self._success(
                action,
                data,
                truncated=truncated,
                warnings=warnings,
            )
        except asyncio.CancelledError:
            raise
        except WebToolError as exc:
            return self._failure(action, exc)

    async def scrape_many(
        self,
        urls: list[str],
        force_js: bool = False,
        wait_selector: Optional[str] = None,
        timeout: Optional[float] = None,
        max_chars: Optional[int] = None,
        captcha_api_key: Optional[str] = None,
        output_format: str = "markdown",
        clean_noise: bool = True,
        deduplicate: bool = True,
    ) -> dict[str, Any]:
        action = "scrape_many"
        try:
            self._validate_legacy_options(
                output_format=output_format,
                captcha_api_key=captcha_api_key,
            )
            _validate_bool(force_js, "force_js")
            _validate_bool(clean_noise, "clean_noise")
            _validate_bool(deduplicate, "deduplicate")
            if not isinstance(urls, list) or not urls:
                raise WebToolError(
                    "INVALID_ARGUMENT",
                    "urls must be a non-empty list",
                )
            if len(urls) > MAX_BATCH_URLS:
                raise WebToolError(
                    "INVALID_ARGUMENT",
                    f"urls exceeds maximum batch size {MAX_BATCH_URLS}",
                )

            selector = (
                None
                if wait_selector is None
                else _validate_string(
                    wait_selector,
                    name="wait_selector",
                    maximum=MAX_WAIT_SELECTOR_CHARS,
                )
            )
            timeout_seconds = (
                self.default_timeout
                if timeout is None
                else _validate_float(
                    timeout,
                    name="timeout",
                    minimum=READ_TIMEOUT_MIN,
                    maximum=READ_TIMEOUT_MAX,
                )
            )
            if max_chars is not None:
                _validate_int(
                    max_chars,
                    name="max_chars",
                    minimum=MAX_CONTENT_CHARS_MIN,
                    maximum=MAX_CONTENT_CHARS_MAX,
                )

            validated_urls: list[str] = []
            for index, item in enumerate(urls):
                if not isinstance(item, str) or not item.strip():
                    raise WebToolError(
                        "INVALID_ARGUMENT",
                        f"urls[{index}] must be a non-empty string",
                    )
                preflight_deadline = time.monotonic() + timeout_seconds
                target = await _await_deadline(
                    self.network_policy.resolve_url(item),
                    preflight_deadline,
                    stage="batch_preflight_dns",
                )
                validated_urls.append(target.url)

            queue: asyncio.Queue[tuple[int, str] | None] = asyncio.Queue()
            results: list[dict[str, Any] | None] = [None] * len(validated_urls)
            for index, item in enumerate(validated_urls):
                queue.put_nowait((index, item))

            worker_count = min(self.max_concurrency, len(validated_urls))
            for _ in range(worker_count):
                queue.put_nowait(None)

            async def worker() -> None:
                while True:
                    entry = await queue.get()
                    if entry is None:
                        return
                    index, item = entry
                    results[index] = await self.scrape(
                        url=item,
                        force_js=force_js,
                        wait_selector=selector,
                        timeout=timeout,
                        max_chars=max_chars,
                        captcha_api_key=None,
                        output_format="markdown",
                        clean_noise=clean_noise,
                        deduplicate=deduplicate,
                    )

            workers = [
                asyncio.create_task(worker())
                for _ in range(worker_count)
            ]
            try:
                await asyncio.gather(*workers)
            except asyncio.CancelledError:
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                raise
            except BaseException:
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                raise

            final_results = [item for item in results if item is not None]
            succeeded = sum(1 for item in final_results if item.get("ok") is True)
            failed = len(final_results) - succeeded
            return self._success(
                action,
                {
                    "requested_count": len(validated_urls),
                    "succeeded_count": succeeded,
                    "failed_count": failed,
                    "results": final_results,
                },
            )
        except asyncio.CancelledError:
            raise
        except WebToolError as exc:
            return self._failure(action, exc)

    async def execute(
        self,
        action: str,
        query: Optional[str] = None,
        url: Optional[str] = None,
        urls: Optional[list[str]] = None,
        output_format: str = "markdown",
        force_js: bool = False,
        wait_selector: Optional[str] = None,
        max_results: int = SEARCH_RESULTS_DEFAULT,
        max_chars: Optional[int] = None,
        timeout: Optional[float] = None,
        captcha_api_key: Optional[str] = None,
        clean_noise: bool = True,
        deduplicate: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        aliases = {
            "read": "scrape",
            "scrape_webpage": "scrape",
            "read_many": "scrape_many",
        }
        canonical = aliases.get(action, action)

        if canonical == "search":
            if query is None:
                return self._failure(
                    canonical,
                    WebToolError(
                        "INVALID_ARGUMENT",
                        "query is required for search",
                    ),
                )
            return await self.search(
                query=query,
                max_results=max_results,
                output_format=output_format,
                timeout=timeout,
            )

        if canonical == "scrape":
            if url is None:
                return self._failure(
                    canonical,
                    WebToolError(
                        "INVALID_ARGUMENT",
                        "url is required for scrape",
                    ),
                )
            return await self.scrape(
                url=url,
                force_js=force_js,
                wait_selector=wait_selector,
                timeout=timeout,
                max_chars=max_chars,
                captcha_api_key=captcha_api_key,
                output_format=output_format,
                clean_noise=clean_noise,
                deduplicate=deduplicate,
            )

        if canonical == "scrape_many":
            if urls is None:
                return self._failure(
                    canonical,
                    WebToolError(
                        "INVALID_ARGUMENT",
                        "urls is required for scrape_many",
                    ),
                )
            return await self.scrape_many(
                urls=urls,
                force_js=force_js,
                wait_selector=wait_selector,
                timeout=timeout,
                max_chars=max_chars,
                captcha_api_key=captcha_api_key,
                output_format=output_format,
                clean_noise=clean_noise,
                deduplicate=deduplicate,
            )

        return self._failure(
            action if isinstance(action, str) and action else "unknown",
            WebToolError("INVALID_ARGUMENT", "unsupported web action"),
        )
