from __future__ import annotations

import asyncio
import ipaddress
import time
from typing import Any, Optional

try:
    from curl_cffi import CurlOpt
    from curl_cffi.requests import AsyncSession
except ImportError:
    CurlOpt = None
    AsyncSession = None

from .config import (
    MAX_PROXY_COUNT,
    MAX_PROXY_TEST_CONCURRENCY,
    MAX_PROXY_URL_CHARS,
)
from .errors import WebToolError, dependency_error
from .network_policy import NetworkPolicy, ResolvedTarget


class ProxyManager:
    def __init__(
        self,
        raw_proxy_list: Optional[list[str]] = None,
        *,
        network_policy: NetworkPolicy,
        session_factory: Any = None,
    ) -> None:
        if raw_proxy_list is None:
            raw_proxy_list = []
        if not isinstance(raw_proxy_list, list):
            raise WebToolError("INVALID_ARGUMENT", "proxy_list must be a list")
        if len(raw_proxy_list) > MAX_PROXY_COUNT:
            raise WebToolError(
                "INVALID_ARGUMENT",
                f"proxy_list exceeds maximum count {MAX_PROXY_COUNT}",
            )
        normalized: list[str] = []
        for index, proxy in enumerate(raw_proxy_list):
            if not isinstance(proxy, str) or not proxy.strip():
                raise WebToolError(
                    "INVALID_ARGUMENT",
                    f"proxy_list[{index}] must be a non-empty string",
                )
            value = proxy.strip()
            if len(value) > MAX_PROXY_URL_CHARS:
                raise WebToolError(
                    "INVALID_ARGUMENT",
                    f"proxy_list[{index}] is too long",
                )
            normalized.append(value)

        self.raw_proxy_list = normalized
        self.valid_proxies: list[str] = []
        self.proxy_latencies: dict[str, float] = {}
        self._policy = network_policy
        self._session_factory = session_factory or AsyncSession
        self._refresh_lock = asyncio.Lock()

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

    async def validate_proxy(self, proxy: str) -> ResolvedTarget:
        return await self._policy.resolve_proxy(proxy)

    async def _test_proxy(
        self,
        proxy: str,
        *,
        test_url: str,
        timeout: float,
    ) -> tuple[str, float] | None:
        if self._session_factory is None:
            raise dependency_error("curl_cffi")
        proxy_target = await self.validate_proxy(proxy)
        await self._policy.resolve_url(test_url)

        started = time.monotonic()
        try:
            async with self._session_factory(
                impersonate="chrome120",
                proxies={"http": proxy, "https": proxy},
                trust_env=False,
                curl_options=self._curl_options(proxy_target),
            ) as session:
                response = await session.get(
                    test_url,
                    timeout=timeout,
                    allow_redirects=False,
                )
            self._policy.verify_primary_ip(
                getattr(response, "primary_ip", None),
                proxy_target,
            )
            if response.status_code != 200:
                return None
            return proxy, time.monotonic() - started
        except asyncio.CancelledError:
            raise
        except WebToolError:
            raise
        except Exception:
            return None

    async def refresh_proxy_pool(
        self,
        *,
        test_url: str = "https://api.ipify.org/?format=json",
        timeout: float = 3.0,
    ) -> int:
        if not self.raw_proxy_list:
            self.valid_proxies = []
            self.proxy_latencies = {}
            return 0
        if self._session_factory is None:
            raise dependency_error("curl_cffi")

        async with self._refresh_lock:
            semaphore = asyncio.Semaphore(
                min(MAX_PROXY_TEST_CONCURRENCY, len(self.raw_proxy_list))
            )

            async def test_one(proxy: str) -> tuple[str, float] | None:
                async with semaphore:
                    try:
                        return await self._test_proxy(
                            proxy,
                            test_url=test_url,
                            timeout=timeout,
                        )
                    except WebToolError:
                        return None

            results = await asyncio.gather(
                *(test_one(proxy) for proxy in self.raw_proxy_list)
            )
            successful = [item for item in results if item is not None]
            successful.sort(key=lambda item: item[1])
            self.valid_proxies = [item[0] for item in successful]
            self.proxy_latencies = {item[0]: item[1] for item in successful}
            return len(self.valid_proxies)

    def remove_proxy(self, proxy: str | None) -> None:
        if not proxy:
            return
        try:
            self.valid_proxies.remove(proxy)
        except ValueError:
            pass
        self.proxy_latencies.pop(proxy, None)

    def get_fastest_proxy(self, top_n: int = 3) -> Optional[dict[str, str]]:
        if not self.valid_proxies:
            return None
        if type(top_n) is not int or top_n < 1:
            raise WebToolError("INVALID_ARGUMENT", "top_n must be a positive integer")
        candidates = self.valid_proxies[: min(top_n, len(self.valid_proxies))]
        selected = candidates[0]
        return {"http": selected, "https": selected}
