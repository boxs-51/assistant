import concurrent.futures
import random
import time
from typing import Dict, List, Optional, Tuple

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

class ProxyManager:
    """Quản lý danh sách proxy, tự động kiểm tra độ trễ và lọc proxy khả dụng."""

    def __init__(self, raw_proxy_list: Optional[List[str]] = None):
        self.raw_proxy_list = raw_proxy_list or []
        self.valid_proxies: List[str] = []
        self.proxy_latencies: Dict[str, float] = {}

    def _test_proxy(
        self, proxy: str, test_url: str = "https://api.ipify.org?format=json", timeout: int = 3
    ) -> Optional[Tuple[str, float]]:
        if not cffi_requests:
            return None
        proxies = {"http": proxy, "https": proxy}
        start_time = time.perf_counter()
        try:
            response = cffi_requests.get(
                test_url, proxies=proxies, timeout=timeout, impersonate="chrome120"
            )
            latency = time.perf_counter() - start_time
            if response.status_code == 200:
                return (proxy, latency)
        except Exception:
            pass
        return None

    def refresh_proxy_pool(
        self, test_url: str = "https://api.ipify.org?format=json", timeout: int = 3, max_workers: int = 20
    ) -> int:
        if not self.raw_proxy_list or not cffi_requests:
            self.valid_proxies = []
            self.proxy_latencies = {}
            return 0

        tested_results: List[Tuple[str, float]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._test_proxy, proxy, test_url, timeout)
                for proxy in self.raw_proxy_list
            ]
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res:
                    tested_results.append(res)

        tested_results.sort(key=lambda x: x[1])
        self.valid_proxies = [item[0] for item in tested_results]
        self.proxy_latencies = {item[0]: item[1] for item in tested_results}
        return len(self.valid_proxies)

    def get_fastest_proxy(self, top_n: int = 3) -> Optional[Dict[str, str]]:
        if not self.valid_proxies:
            return None
        candidates = self.valid_proxies[:top_n]
        selected = random.choice(candidates)
        return {"http": selected, "https": selected}