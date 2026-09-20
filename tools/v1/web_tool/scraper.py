from __future__ import annotations

import asyncio
from typing import Dict, Optional, Tuple

try:
    from curl_cffi.requests import AsyncSession
except ImportError:
    AsyncSession = None

try:
    from playwright.async_api import async_playwright, Browser, Playwright
except ImportError:
    async_playwright = None

from .extractors import extract_tables_and_charts
from .stealth import WebToolStealth

class WebScraper:
    """Quản lý các cơ chế cào trang web tĩnh (Static) và động (Dynamic Playwright Stealth)."""

    def __init__(self, max_response_bytes: int = 10 * 1024 * 1024, max_concurrency: int = 5):
        self.max_response_bytes = max_response_bytes
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._lifecycle_lock = asyncio.Lock()
        self._owner_loop: Optional[asyncio.AbstractEventLoop] = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        """Close browser + Playwright exactly once on their owning event loop."""
        if self._owner_loop is not None and asyncio.get_running_loop() is not self._owner_loop:
            raise RuntimeError(
                "WebScraper Playwright resources must be closed on the event loop "
                "that created them."
            )

        async with self._lifecycle_lock:
            browser = self._browser
            playwright = self._playwright

            if browser is None and playwright is None:
                return

            first_error: Optional[Exception] = None

            if browser is not None:
                try:
                    await browser.close()
                except Exception as exc:
                    first_error = exc
                finally:
                    self._browser = None

            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
                finally:
                    self._playwright = None

            if first_error is not None:
                raise first_error

    async def _get_browser(self) -> Browser:
        """Return the single Chromium instance owned by this event loop."""
        async with self._lifecycle_lock:
            current_loop = asyncio.get_running_loop()
            if self._owner_loop is not None and current_loop is not self._owner_loop:
                raise RuntimeError(
                    "WebScraper Playwright resources cannot cross event-loop ownership."
                )

            if self._browser and self._browser.is_connected():
                return self._browser

            if self._playwright is None:
                playwright = await async_playwright().start()
                self._playwright = playwright
                self._owner_loop = current_loop

            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=launch_args,
            )
            return self._browser

    async def fetch_static(
        self, url: str, timeout: int, profile: str, proxies: Optional[Dict[str, str]]
    ) -> Tuple[Optional[str], Optional[int], str]:
        if not AsyncSession:
            return None, None, "Thư viện 'curl_cffi' chưa được cài đặt."
        try:
            headers = {
                "Accept": "text/html,application/xhtmlxml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
                "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Upgrade-Insecure-Requests": "1"
            }
            async with AsyncSession(impersonate=profile, proxies=proxies) as session:
                response = await session.get(url, headers=headers, timeout=timeout, stream=True)
                if response.status_code == 403:
                    return None, 403, "HTTP 403 Forbidden"

                response.raise_for_status()

                content_type = response.headers.get("Content-Type", "").lower()
                if not any(t in content_type for t in ["text/", "html", "json", "xml"]):
                    return None, response.status_code, f"Tệp không phải văn bản (Content-Type: {content_type})"

                content_bytes = bytearray()
                async for chunk in response.aiter_content(chunk_size=8192):
                    content_bytes.extend(chunk)
                    if len(content_bytes) > self.max_response_bytes:
                        return None, response.status_code, "Vượt giới hạn dung lượng cho phép."

                encoding = (
                    response.encoding if isinstance(response.encoding, str) and response.encoding
                    else response.apparent_encoding if isinstance(response.apparent_encoding, str) and response.apparent_encoding
                    else "utf-8"
                )
                return content_bytes.decode(encoding, errors="replace"), response.status_code, "OK"

        except Exception as e:
            return None, None, str(e)

    async def fetch_dynamic_js_stealth(
        self,
        url: str,
        timeout: int,
        wait_selector: Optional[str] = None,
        proxy: Optional[str] = None,
        captcha_api_key: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[Dict[str, str]], Optional[dict], str]:
        if not async_playwright:
            return None, None, None, "Thư viện 'playwright' chưa được cài đặt."
        
        async with self._semaphore:
            context = None
            try:
                browser = await self._get_browser()
                context_options = {
                    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "viewport": {"width": 1920, "height": 1080},
                    "locale": "vi-VN",
                    "timezone_id": "Asia/Ho_Chi_Minh",
                }

                if proxy:
                    context_options["proxy"] = {"server": proxy}

                context = await browser.new_context(**context_options)
                page = await context.new_page()

                await WebToolStealth.apply_stealth_scripts(page)

                await page.route(
                    "**/*.{png,jpg,jpeg,svg,woff,woff2,ttf}",
                    lambda route: route.abort(),
                )

                await page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                await WebToolStealth.simulate_human_behavior(page)

                if wait_selector:
                    try:
                        await page.wait_for_selector(wait_selector, timeout=timeout * 1000, state="visible")
                    except Exception:
                        pass
                else:
                    for _ in range(4):
                        html_check = await page.content()
                        title_check = (await page.title()).lower()
                        is_cf = WebToolStealth.is_captcha_or_cf_present(html_check) or "chờ một chút" in title_check or "just a moment" in title_check
                        
                        if not is_cf:
                            break
                        
                        if captcha_api_key:
                            if await WebToolStealth.extract_and_solve_captcha(page, url, captcha_api_key):
                                await page.wait_for_load_state("networkidle", timeout=10000)
                                break
                        
                        await asyncio.sleep(2.5)
                        await WebToolStealth.simulate_human_behavior(page)

                cookies_list = await context.cookies()
                cookies_dict = {cookie["name"]: cookie["value"] for cookie in cookies_list}
                html_content = await page.content()
                
                structured_data = await extract_tables_and_charts(html_content, page_obj=page)

                if WebToolStealth.is_captcha_or_cf_present(html_content):
                    return html_content, cookies_dict, structured_data, "Cảnh báo: Trang web yêu cầu xác minh CAPTCHA thủ công."

                return html_content, cookies_dict, structured_data, "OK"

            except Exception as e:
                return None, None, None, f"Lỗi Playwright Stealth: {str(e)}"
            finally:
                if context:
                    try:
                        await context.close()
                    except Exception:
                        pass
