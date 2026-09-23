import asyncio
import hashlib
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import tools.v1.web_tool as web_package
import tools.v1.web_tool.core as core_module
import tools.v1.web_tool.proxy as proxy_module
import tools.v1.web_tool.scraper as scraper_module
from tools.v1._shared.metadata import validate_tool_manifest_v2
from tools.v1.web_tool import TOOL_METADATA, WebTool, run
from tools.v1.web_tool.cap_solver_handler import CapSolverHandler
from tools.v1.web_tool.config import (
    MAX_BATCH_URLS,
    MAX_CONTENT_CHARS_MAX,
    MAX_QUERY_CHARS,
    MAX_REDIRECTS,
    MAX_RENDERED_HTML_CHARS,
    MAX_RETRIES,
    MAX_STATIC_RESPONSE_BYTES,
    MAX_TABLE_ROWS,
    MAX_URL_CHARS,
    READ_TIMEOUT_MAX,
    SEARCH_RESULTS_MAX,
    WEB_TOOL_NAME,
    WEB_TOOL_VERSION,
)
from tools.v1.web_tool.errors import WebToolError
from tools.v1.web_tool.extractors import extract_tables_and_charts
from tools.v1.web_tool.network_policy import NetworkPolicy, ResolvedTarget
from tools.v1.web_tool.proxy import ProxyManager
from tools.v1.web_tool.scraper import WebScraper
from tools.v1.web_tool.searcher import WebSearcher


PUBLIC_IP = "93.184.216.34"
PUBLIC_IPV6 = "2606:2800:220:1:248:1893:25c8:1946"


class FakeResolver:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}
        self.calls = []

    async def __call__(self, host, port):
        self.calls.append((host, port))
        value = self.mapping.get(host, [PUBLIC_IP])
        if isinstance(value, BaseException):
            raise value
        return list(value)


class FakeResponse:
    def __init__(
        self,
        *,
        status=200,
        body=b"<html><body>ok</body></html>",
        headers=None,
        primary_ip=PUBLIC_IP,
        encoding="utf-8",
    ):
        self.status_code = status
        self.body = body
        self.headers = headers or {"Content-Type": "text/html"}
        self.primary_ip = primary_ip
        self.encoding = encoding

    async def aiter_content(self, chunk_size=8192):
        for index in range(0, len(self.body), chunk_size):
            yield self.body[index : index + chunk_size]


class FakeSession:
    def __init__(self, factory):
        self.factory = factory

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, **kwargs):
        self.factory.calls.append(("get", url, kwargs))
        if not self.factory.responses:
            raise AssertionError("unexpected GET")
        response = self.factory.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def post(self, url, **kwargs):
        self.factory.calls.append(("post", url, kwargs))
        if not self.factory.responses:
            raise AssertionError("unexpected POST")
        response = self.factory.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeSessionFactory:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.session_kwargs = []

    def __call__(self, **kwargs):
        self.session_kwargs.append(kwargs)
        return FakeSession(self)


class FakeRoute:
    def __init__(self, url, resource_type="document"):
        self.request = SimpleNamespace(url=url, resource_type=resource_type)
        self.continued = False
        self.aborted = False

    async def continue_(self):
        self.continued = True

    async def abort(self):
        self.aborted = True


class FakeWebSocketRoute:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakePWRequest:
    def __init__(self, url, redirected_from=None):
        self.url = url
        self.redirected_from = redirected_from


class FakePWResponse:
    def __init__(
        self,
        url="https://example.com/",
        status=200,
        headers=None,
        request=None,
    ):
        self.status = status
        self.headers = headers or {"content-type": "text/html"}
        self.request = request or FakePWRequest(url)


class FakePage:
    def __init__(
        self,
        *,
        html="<html><head><title>Example</title></head><body>Hello</body></html>",
        url="https://example.com/",
        selector_error=None,
    ):
        self._html = html
        self.url = url
        self.selector_error = selector_error
        self.init_scripts = []
        self.goto_calls = []
        self.wait_calls = []
        self.evaluate = AsyncMock(return_value=[])

    async def add_init_script(self, script):
        self.init_scripts.append(script)

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        self.url = url
        return FakePWResponse(url=url)

    async def wait_for_selector(self, selector, **kwargs):
        self.wait_calls.append((selector, kwargs))
        if self.selector_error is not None:
            raise self.selector_error
        return object()

    async def content(self):
        return self._html

    async def title(self):
        return "Example"


class FakeContext:
    def __init__(self, page):
        self.page = page
        self.route_handler = None
        self.ws_handler = None
        self.closed = 0

    async def route(self, pattern, handler):
        self.route_handler = handler

    async def route_web_socket(self, pattern, handler):
        self.ws_handler = handler

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed += 1

    async def cookies(self):
        return []


class FakeBrowser:
    def __init__(self, page):
        self.page = page
        self.contexts = []
        self.context_options = []
        self.closed = 0

    def is_connected(self):
        return True

    async def new_context(self, **kwargs):
        self.context_options.append(kwargs)
        context = FakeContext(self.page)
        self.contexts.append(context)
        return context

    async def close(self):
        self.closed += 1


class FakeChromium:
    def __init__(self, browser):
        self.browser = browser
        self.launch_kwargs = []
        self.launch_count = 0

    async def launch(self, **kwargs):
        self.launch_count += 1
        self.launch_kwargs.append(kwargs)
        await asyncio.sleep(0)
        return self.browser


class FakePlaywright:
    def __init__(self, browser):
        self.chromium = FakeChromium(browser)
        self.stopped = 0

    async def stop(self):
        self.stopped += 1


class FakePlaywrightFactory:
    def __init__(self, page=None):
        self.page = page or FakePage()
        self.browser = FakeBrowser(self.page)
        self.playwright = FakePlaywright(self.browser)
        self.start_count = 0

    def __call__(self):
        owner = self

        class Starter:
            async def start(self_inner):
                owner.start_count += 1
                await asyncio.sleep(0)
                return owner.playwright

        return Starter()


def make_policy(mapping=None):
    return NetworkPolicy(FakeResolver(mapping))


def static_source(
    *,
    html="<html><head><title>Static Title</title></head><body>content</body></html>",
    rendered=False,
    structured=None,
):
    return {
        "html": html,
        "status_code": 200,
        "requested_url": "https://example.com/",
        "final_url": "https://example.com/",
        "redirect_chain": [],
        "content_type": "text/html",
        "proxy_used": False,
        "rendered": rendered,
        "wait_selector_satisfied": None,
        "structured_data": (
            {"tables": [], "charts": []}
            if structured is None
            else structured
        ),
    }


class TestNetworkPolicy(unittest.IsolatedAsyncioTestCase):
    async def test_blocks_non_global_literal_addresses(self):
        policy = make_policy()
        for url in (
            "http://127.0.0.1/",
            "http://10.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://[fe80::1]/",
            "http://[::ffff:127.0.0.1]/",
        ):
            with self.subTest(url=url):
                with self.assertRaises(WebToolError) as ctx:
                    await policy.resolve_url(url)
                self.assertEqual(ctx.exception.code, "WEB_URL_BLOCKED")

    async def test_blocks_local_names_credentials_and_bad_scheme(self):
        policy = make_policy()
        for url in (
            "http://localhost/",
            "http://thing.local/",
            "http://user:pass@example.com/",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url):
                with self.assertRaises(WebToolError):
                    await policy.resolve_url(url)

    async def test_mixed_dns_is_blocked(self):
        policy = make_policy(
            {"mixed.example": [PUBLIC_IP, "10.0.0.8"]}
        )
        with self.assertRaises(WebToolError) as ctx:
            await policy.resolve_url("https://mixed.example/")
        self.assertEqual(ctx.exception.code, "WEB_URL_BLOCKED")

    async def test_public_dns_normalizes_and_strips_fragment(self):
        policy = make_policy()
        target = await policy.resolve_url(
            "HTTPS://Example.COM/path?q=1#fragment"
        )
        self.assertEqual(target.url, "https://example.com/path?q=1")
        self.assertEqual(target.port, 443)
        self.assertEqual(target.addresses, (PUBLIC_IP,))

    async def test_private_redirect_is_blocked_before_follow(self):
        policy = make_policy()
        with self.assertRaises(WebToolError) as ctx:
            await policy.resolve_redirect(
                "https://example.com/",
                "http://127.0.0.1/secret",
            )
        self.assertEqual(ctx.exception.code, "WEB_REDIRECT_BLOCKED")

    async def test_connected_ip_must_match_validated_set(self):
        target = await make_policy().resolve_url("https://example.com/")
        with self.assertRaises(WebToolError) as ctx:
            NetworkPolicy.verify_primary_ip("8.8.8.8", target)
        self.assertEqual(ctx.exception.code, "WEB_NETWORK_ERROR")

    async def test_unexpected_resolver_error_is_structured(self):
        resolver = FakeResolver(
            {"broken.example": RuntimeError("resolver internals")}
        )
        policy = NetworkPolicy(resolver)
        with self.assertRaises(WebToolError) as ctx:
            await policy.resolve_url("https://broken.example/")
        self.assertEqual(ctx.exception.code, "WEB_DNS_FAILED")
        self.assertEqual(
            ctx.exception.details["exception_type"],
            "RuntimeError",
        )
        self.assertNotIn("resolver internals", str(ctx.exception.details))

    async def test_proxy_resolution_sanitizes_credentials_and_blocks_private(self):
        policy = make_policy({"proxy.example": [PUBLIC_IP]})
        target = await policy.resolve_proxy(
            "http://user:secret@proxy.example:8080"
        )
        self.assertEqual(target.url, "http://proxy.example:8080")
        self.assertEqual(target.host, "proxy.example")
        self.assertEqual(target.addresses, (PUBLIC_IP,))
        self.assertNotIn("user", target.url)
        self.assertNotIn("secret", target.url)

        with self.assertRaises(WebToolError) as ctx:
            await policy.resolve_proxy("http://127.0.0.1:8080")
        self.assertEqual(ctx.exception.code, "WEB_URL_BLOCKED")

    async def test_ipv4_mapped_public_primary_ip_matches_ipv4_dns(self):
        target = ResolvedTarget(
            url="https://example.com/",
            scheme="https",
            host="example.com",
            port=443,
            addresses=("8.8.8.8",),
        )
        NetworkPolicy.verify_primary_ip("::ffff:8.8.8.8", target)



class TestStaticFetcher(unittest.IsolatedAsyncioTestCase):
    async def test_static_success_is_bounded_and_disables_environment_proxy(self):
        factory = FakeSessionFactory(
            [
                FakeResponse(
                    body=b"<html><body>Hello</body></html>",
                    headers={"Content-Type": "text/html"},
                )
            ]
        )
        scraper = WebScraper(
            network_policy=make_policy(),
            session_factory=factory,
        )
        result = await scraper.fetch_static(
            url="https://example.com/",
            deadline=time.monotonic() + 5,
            profile="chrome120",
        )
        self.assertEqual(result["status_code"], 200)
        self.assertFalse(result["proxy_used"])
        self.assertEqual(result["final_url"], "https://example.com/")
        self.assertFalse(factory.session_kwargs[0]["trust_env"])
        self.assertFalse(factory.calls[0][2]["allow_redirects"])

    async def test_static_primary_ip_mismatch_fails(self):
        factory = FakeSessionFactory(
            [FakeResponse(primary_ip="8.8.8.8")]
        )
        scraper = WebScraper(
            network_policy=make_policy(),
            session_factory=factory,
        )
        with self.assertRaises(WebToolError) as ctx:
            await scraper.fetch_static(
                url="https://example.com/",
                deadline=time.monotonic() + 5,
                profile="chrome120",
            )
        self.assertEqual(ctx.exception.code, "WEB_NETWORK_ERROR")

    async def test_redirect_to_private_is_blocked_before_second_get(self):
        factory = FakeSessionFactory(
            [
                FakeResponse(
                    status=302,
                    headers={
                        "Content-Type": "text/html",
                        "Location": "http://127.0.0.1/private",
                    },
                )
            ]
        )
        scraper = WebScraper(
            network_policy=make_policy(),
            session_factory=factory,
        )
        with self.assertRaises(WebToolError) as ctx:
            await scraper.fetch_static(
                url="https://example.com/",
                deadline=time.monotonic() + 5,
                profile="chrome120",
            )
        self.assertEqual(ctx.exception.code, "WEB_REDIRECT_BLOCKED")
        self.assertEqual(len(factory.calls), 1)

    async def test_body_hard_limit_and_binary_content_fail(self):
        factory = FakeSessionFactory(
            [
                FakeResponse(
                    headers={
                        "Content-Type": "text/html",
                        "Content-Length": str(MAX_STATIC_RESPONSE_BYTES + 1),
                    }
                )
            ]
        )
        scraper = WebScraper(
            network_policy=make_policy(),
            session_factory=factory,
        )
        with self.assertRaises(WebToolError) as ctx:
            await scraper.fetch_static(
                url="https://example.com/",
                deadline=time.monotonic() + 5,
                profile="chrome120",
            )
        self.assertEqual(ctx.exception.code, "WEB_RESPONSE_TOO_LARGE")

        factory = FakeSessionFactory(
            [FakeResponse(headers={"Content-Type": "image/png"})]
        )
        scraper = WebScraper(
            network_policy=make_policy(),
            session_factory=factory,
        )
        with self.assertRaises(WebToolError) as ctx:
            await scraper.fetch_static(
                url="https://example.com/",
                deadline=time.monotonic() + 5,
                profile="chrome120",
            )
        self.assertEqual(ctx.exception.code, "WEB_UNSUPPORTED_CONTENT_TYPE")

    async def test_static_dns_obeys_total_deadline_before_network_start(self):
        async def slow_resolver(host, port):
            await asyncio.sleep(1)
            return [PUBLIC_IP]

        factory = FakeSessionFactory([])
        scraper = WebScraper(
            network_policy=NetworkPolicy(slow_resolver),
            session_factory=factory,
        )
        with self.assertRaises(WebToolError) as ctx:
            await scraper.fetch_static(
                url="https://example.com/",
                deadline=time.monotonic() + 0.01,
                profile="chrome120",
            )
        self.assertEqual(ctx.exception.code, "WEB_TIMEOUT")
        self.assertEqual(factory.calls, [])

    async def test_static_proxy_is_dns_pinned_and_connected_ip_verified(self):
        proxy_url = "http://user:secret@proxy.example:8080"
        policy = make_policy(
            {
                "example.com": [PUBLIC_IP],
                "proxy.example": [PUBLIC_IP],
            }
        )
        factory = FakeSessionFactory(
            [FakeResponse(primary_ip=PUBLIC_IP)]
        )
        scraper = WebScraper(
            network_policy=policy,
            session_factory=factory,
        )
        with patch.object(
            scraper_module,
            "CurlOpt",
            SimpleNamespace(RESOLVE="RESOLVE"),
        ):
            result = await scraper.fetch_static(
                url="https://example.com/",
                deadline=time.monotonic() + 5,
                profile="chrome120",
                proxy={"http": proxy_url, "https": proxy_url},
            )
        self.assertTrue(result["proxy_used"])
        self.assertEqual(
            factory.session_kwargs[0]["curl_options"]["RESOLVE"],
            [f"proxy.example:8080:{PUBLIC_IP}"],
        )
        self.assertFalse(factory.session_kwargs[0]["trust_env"])

        mismatch_factory = FakeSessionFactory(
            [FakeResponse(primary_ip="8.8.8.8")]
        )
        mismatch = WebScraper(
            network_policy=policy,
            session_factory=mismatch_factory,
        )
        with patch.object(
            scraper_module,
            "CurlOpt",
            SimpleNamespace(RESOLVE="RESOLVE"),
        ):
            with self.assertRaises(WebToolError) as ctx:
                await mismatch.fetch_static(
                    url="https://example.com/",
                    deadline=time.monotonic() + 5,
                    profile="chrome120",
                    proxy={"http": proxy_url, "https": proxy_url},
                )
        self.assertEqual(ctx.exception.code, "WEB_NETWORK_ERROR")


class TestBrowserFetcher(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.policy = make_policy()
        self.factory = FakePlaywrightFactory()
        self.scraper = WebScraper(
            network_policy=self.policy,
            playwright_factory=self.factory,
        )

    async def asyncTearDown(self):
        await self.scraper.close()

    async def test_browser_security_defaults_and_routes(self):
        result = await self.scraper.fetch_dynamic(
            url="https://example.com/",
            deadline=time.monotonic() + 5,
        )
        self.assertTrue(result["rendered"])
        launch_args = self.factory.playwright.chromium.launch_kwargs[0]["args"]
        self.assertNotIn("--no-sandbox", launch_args)
        self.assertNotIn("--disable-web-security", launch_args)
        self.assertFalse(
            any("IsolateOrigins" in value for value in launch_args)
        )

        context = self.factory.browser.contexts[0]
        options = self.factory.browser.context_options[0]
        self.assertEqual(options["service_workers"], "block")
        self.assertFalse(options["accept_downloads"])

        private_route = FakeRoute("http://127.0.0.1/")
        await context.route_handler(private_route)
        self.assertTrue(private_route.aborted)
        self.assertFalse(private_route.continued)

        public_route = FakeRoute("https://example.com/script.js", "script")
        await context.route_handler(public_route)
        self.assertTrue(public_route.continued)

        media_route = FakeRoute("https://example.com/a.png", "image")
        await context.route_handler(media_route)
        self.assertTrue(media_route.aborted)

        ws = FakeWebSocketRoute()
        await context.ws_handler(ws)
        self.assertTrue(ws.closed)

    async def test_curl_resolve_formats_ipv6_and_multiple_addresses(self):
        if scraper_module.CurlOpt is None:
            self.skipTest("curl_cffi not installed")
        target = ResolvedTarget(
            url="https://example.com/",
            scheme="https",
            host="example.com",
            port=443,
            addresses=(PUBLIC_IPV6, PUBLIC_IP),
        )
        options = WebScraper._curl_options(target)
        entries = next(iter(options.values()))
        self.assertEqual(
            entries,
            [
                "example.com:443:"
                f"[{PUBLIC_IPV6}],{PUBLIC_IP}"
            ],
        )
        search_options = WebSearcher._curl_options(target)
        search_entries = next(iter(search_options.values()))
        self.assertEqual(search_entries, entries)

    async def test_dynamic_http_binary_and_declared_size_fail_closed(self):
        cases = [
            (
                FakePWResponse(status=404),
                "WEB_HTTP_ERROR",
            ),
            (
                FakePWResponse(
                    headers={"content-type": "image/png"}
                ),
                "WEB_UNSUPPORTED_CONTENT_TYPE",
            ),
            (
                FakePWResponse(
                    headers={
                        "content-type": "text/html",
                        "content-length": str(
                            MAX_STATIC_RESPONSE_BYTES + 1
                        ),
                    }
                ),
                "WEB_RESPONSE_TOO_LARGE",
            ),
        ]
        for response, code in cases:
            with self.subTest(code=code):
                page = FakePage()
                page.goto = AsyncMock(return_value=response)
                factory = FakePlaywrightFactory(page)
                scraper = WebScraper(
                    network_policy=self.policy,
                    playwright_factory=factory,
                )
                try:
                    with self.assertRaises(WebToolError) as ctx:
                        await scraper.fetch_dynamic(
                            url="https://example.com/",
                            deadline=time.monotonic() + 5,
                        )
                    self.assertEqual(ctx.exception.code, code)
                finally:
                    await scraper.close()

    async def test_missing_websocket_routing_api_fails_closed(self):
        page = FakePage()
        factory = FakePlaywrightFactory(page)
        context = FakeContext(page)
        context.route_web_socket = None
        factory.browser.new_context = AsyncMock(
            return_value=context
        )
        scraper = WebScraper(
            network_policy=self.policy,
            playwright_factory=factory,
        )
        try:
            with self.assertRaises(WebToolError) as ctx:
                await scraper.fetch_dynamic(
                    url="https://example.com/",
                    deadline=time.monotonic() + 5,
                )
            self.assertEqual(
                ctx.exception.code,
                "DEPENDENCY_UNAVAILABLE",
            )
            self.assertEqual(context.closed, 1)
        finally:
            await scraper.close()

    async def test_authenticated_proxy_settings_strip_credentials_from_server(self):
        settings = WebScraper._playwright_proxy_settings(
            "http://user:p%40ss@proxy.example:8080"
        )
        self.assertEqual(
            settings["server"],
            "http://proxy.example:8080",
        )
        self.assertEqual(settings["username"], "user")
        self.assertEqual(settings["password"], "p@ss")
        self.assertNotIn("user", settings["server"])
        self.assertNotIn("p%40ss", settings["server"])

    async def test_dynamic_proxy_uses_validated_ip_without_echoing_credentials(self):
        proxy_url = "http://user:p%40ss@proxy.example:8080"
        factory = FakePlaywrightFactory()
        scraper = WebScraper(
            network_policy=make_policy(
                {
                    "proxy.example": [PUBLIC_IP],
                    "example.com": [PUBLIC_IP],
                }
            ),
            playwright_factory=factory,
        )
        try:
            result = await scraper.fetch_dynamic(
                url="https://example.com/",
                deadline=time.monotonic() + 5,
                proxy=proxy_url,
            )
            self.assertTrue(result["proxy_used"])
            proxy_options = factory.browser.context_options[0]["proxy"]
            self.assertEqual(
                proxy_options["server"],
                f"http://{PUBLIC_IP}:8080",
            )
            self.assertEqual(proxy_options["username"], "user")
            self.assertEqual(proxy_options["password"], "p@ss")
            self.assertNotIn("user", proxy_options["server"])
            self.assertNotIn("p%40ss", proxy_options["server"])
        finally:
            await scraper.close()

    async def test_browser_launch_cancellation_stops_playwright_owner(self):
        factory = FakePlaywrightFactory()
        factory.playwright.chromium.launch = AsyncMock(
            side_effect=asyncio.CancelledError()
        )
        scraper = WebScraper(
            network_policy=self.policy,
            playwright_factory=factory,
        )
        with self.assertRaises(asyncio.CancelledError):
            await scraper._get_browser()
        self.assertEqual(factory.playwright.stopped, 1)
        self.assertIsNone(scraper._playwright)
        self.assertIsNone(scraper._owner_loop)

    async def test_raw_browser_backend_error_is_structured(self):
        page = FakePage()
        page.content = AsyncMock(
            side_effect=RuntimeError("browser internals")
        )
        factory = FakePlaywrightFactory(page)
        scraper = WebScraper(
            network_policy=self.policy,
            playwright_factory=factory,
        )
        try:
            with self.assertRaises(WebToolError) as ctx:
                await scraper.fetch_dynamic(
                    url="https://example.com/",
                    deadline=time.monotonic() + 5,
                )
            self.assertEqual(
                ctx.exception.code,
                "WEB_NETWORK_ERROR",
            )
            self.assertEqual(
                ctx.exception.details["exception_type"],
                "RuntimeError",
            )
            self.assertNotIn(
                "browser internals",
                str(ctx.exception.details),
            )
        finally:
            await scraper.close()

    async def test_requested_selector_failure_is_observable(self):
        factory = FakePlaywrightFactory(
            FakePage(selector_error=RuntimeError("not visible"))
        )
        scraper = WebScraper(
            network_policy=self.policy,
            playwright_factory=factory,
        )
        try:
            with self.assertRaises(WebToolError) as ctx:
                await scraper.fetch_dynamic(
                    url="https://example.com/",
                    deadline=time.monotonic() + 5,
                    wait_selector="#ready",
                )
            self.assertEqual(ctx.exception.code, "WEB_SELECTOR_TIMEOUT")
            self.assertEqual(factory.browser.contexts[0].closed, 1)
        finally:
            await scraper.close()

    async def test_challenge_is_reported_without_solver(self):
        factory = FakePlaywrightFactory(
            FakePage(html="<html>cf-turnstile verify you are human</html>")
        )
        scraper = WebScraper(
            network_policy=self.policy,
            playwright_factory=factory,
        )
        try:
            with self.assertRaises(WebToolError) as ctx:
                await scraper.fetch_dynamic(
                    url="https://example.com/",
                    deadline=time.monotonic() + 5,
                )
            self.assertEqual(ctx.exception.code, "WEB_CHALLENGE_REQUIRED")
        finally:
            await scraper.close()

    async def test_rendered_html_hard_limit(self):
        factory = FakePlaywrightFactory(
            FakePage(html="x" * (MAX_RENDERED_HTML_CHARS + 1))
        )
        scraper = WebScraper(
            network_policy=self.policy,
            playwright_factory=factory,
        )
        try:
            with self.assertRaises(WebToolError) as ctx:
                await scraper.fetch_dynamic(
                    url="https://example.com/",
                    deadline=time.monotonic() + 5,
                )
            self.assertEqual(ctx.exception.code, "WEB_RESPONSE_TOO_LARGE")
        finally:
            await scraper.close()

    async def test_concurrent_browser_initialization_has_single_owner(self):
        browsers = await asyncio.gather(
            *(self.scraper._get_browser() for _ in range(8))
        )
        self.assertTrue(all(item is browsers[0] for item in browsers))
        self.assertEqual(self.factory.start_count, 1)
        self.assertEqual(self.factory.playwright.chromium.launch_count, 1)

    async def test_close_is_idempotent(self):
        await self.scraper._get_browser()
        await self.scraper.close()
        await self.scraper.close()
        self.assertEqual(self.factory.browser.closed, 1)
        self.assertEqual(self.factory.playwright.stopped, 1)

    async def test_browser_redirect_chain_hard_limit(self):
        previous = None
        for index in range(MAX_REDIRECTS + 1):
            previous = FakePWRequest(
                f"https://example.com/r{index}",
                redirected_from=previous,
            )
        response = SimpleNamespace(
            request=FakePWRequest(
                "https://example.com/final",
                redirected_from=previous,
            )
        )
        with self.assertRaises(WebToolError) as ctx:
            await self.scraper._browser_redirect_chain(response)
        self.assertEqual(ctx.exception.code, "WEB_REDIRECT_LIMIT")



class TestSearcherAndProxy(unittest.IsolatedAsyncioTestCase):
    async def test_search_dns_obeys_total_timeout(self):
        async def slow_resolver(host, port):
            await asyncio.sleep(1)
            return [PUBLIC_IP]

        factory = FakeSessionFactory([])
        searcher = WebSearcher(
            network_policy=NetworkPolicy(slow_resolver),
            session_factory=factory,
        )
        with self.assertRaises(WebToolError) as ctx:
            await searcher.search(
                query="python",
                max_results=5,
                timeout=0.01,
            )
        self.assertEqual(ctx.exception.code, "WEB_TIMEOUT")
        self.assertEqual(factory.calls, [])

    async def test_search_success_empty_and_failure_are_distinct(self):
        html = b"""
        <html><body>
          <div class="result">
            <a class="result__a" href="https://python.org/">Python</a>
            <div class="result__snippet">Learn Python</div>
          </div>
        </body></html>
        """
        factory = FakeSessionFactory([FakeResponse(body=html)])
        searcher = WebSearcher(
            network_policy=make_policy(),
            session_factory=factory,
        )
        results = await searcher.search(
            query="python",
            max_results=5,
            timeout=5,
        )
        self.assertEqual(
            results,
            [
                {
                    "title": "Python",
                    "url": "https://python.org/",
                    "snippet": "Learn Python",
                }
            ],
        )

        empty_factory = FakeSessionFactory(
            [FakeResponse(body=b"<html><body>No results</body></html>")]
        )
        empty_searcher = WebSearcher(
            network_policy=make_policy(),
            session_factory=empty_factory,
        )
        self.assertEqual(
            await empty_searcher.search(
                query="none",
                max_results=5,
                timeout=5,
            ),
            [],
        )

        failed = WebSearcher(
            network_policy=make_policy(),
            session_factory=FakeSessionFactory(
                [FakeResponse(status=503)]
            ),
        )
        with self.assertRaises(WebToolError) as ctx:
            await failed.search(query="x", max_results=5, timeout=5)
        self.assertEqual(ctx.exception.code, "WEB_SEARCH_PROVIDER_FAILED")

    async def test_search_malformed_success_page_is_provider_failure(self):
        searcher = WebSearcher(
            network_policy=make_policy(),
            session_factory=FakeSessionFactory(
                [
                    FakeResponse(
                        body=b"<html><body>temporary provider error</body></html>"
                    )
                ]
            ),
        )
        with self.assertRaises(WebToolError) as ctx:
            await searcher.search(
                query="x",
                max_results=5,
                timeout=5,
            )
        self.assertEqual(
            ctx.exception.code,
            "WEB_SEARCH_PROVIDER_FAILED",
        )

    async def test_search_filters_result_url_credentials(self):
        html = b"""
        <div class="result">
          <a class="result__a" href="https://u:p@example.com/">Bad</a>
        </div>
        """
        searcher = WebSearcher(
            network_policy=make_policy(),
            session_factory=FakeSessionFactory([FakeResponse(body=html)]),
        )
        results = await searcher.search(
            query="x",
            max_results=5,
            timeout=5,
        )
        self.assertEqual(results, [])

    async def test_proxy_url_path_query_and_fragment_are_rejected(self):
        manager = ProxyManager(
            [],
            network_policy=make_policy(),
            session_factory=FakeSessionFactory([]),
        )
        for proxy in (
            "http://8.8.8.8:8080/path",
            "http://8.8.8.8:8080/?x=1",
            "http://8.8.8.8:8080/#fragment",
        ):
            with self.subTest(proxy=proxy):
                with self.assertRaises(WebToolError) as ctx:
                    await manager.validate_proxy(proxy)
                self.assertEqual(
                    ctx.exception.code,
                    "INVALID_ARGUMENT",
                )

    async def test_proxy_refresh_is_async_bounded_and_private_endpoint_rejected(self):
        factory = FakeSessionFactory(
            [
                FakeResponse(primary_ip="1.1.1.1"),
                FakeResponse(primary_ip="8.8.8.8"),
            ]
        )
        manager = ProxyManager(
            [
                "http://1.1.1.1:8080",
                "http://8.8.8.8:8080",
            ],
            network_policy=make_policy(),
            session_factory=factory,
        )
        count = await manager.refresh_proxy_pool(timeout=1)
        self.assertEqual(count, 2)

        private = ProxyManager(
            ["http://127.0.0.1:8080"],
            network_policy=make_policy(),
            session_factory=FakeSessionFactory([]),
        )
        self.assertEqual(await private.refresh_proxy_pool(timeout=1), 0)


class TestWebToolStructuredContract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tool = WebTool(
            network_policy=make_policy(),
            min_content_length=1,
        )

    async def asyncTearDown(self):
        await self.tool.close()

    def assert_ok(self, result, action):
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool"], WEB_TOOL_NAME)
        self.assertEqual(result["action"], action)
        self.assertEqual(result["meta"]["version"], WEB_TOOL_VERSION)
        self.assertIsNone(result["error"])
        json.dumps(result)
        return result["data"]

    def assert_error(self, result, action, code):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["tool"], WEB_TOOL_NAME)
        self.assertEqual(result["action"], action)
        self.assertEqual(result["error"]["code"], code)
        self.assertIsNone(result["data"])
        json.dumps(result)

    async def test_metadata_v2_logical_capability_contract(self):
        manifest = validate_tool_manifest_v2(TOOL_METADATA)

        self.assertEqual(manifest["manifest_version"], "2.0")
        self.assertEqual(manifest["name"], WEB_TOOL_NAME)
        self.assertEqual(manifest["version"], WEB_TOOL_VERSION)
        self.assertIs(manifest["expose_root"], False)
        self.assertNotIn("metadata_version", manifest)
        self.assertNotIn("capabilities", manifest)

        exports = {
            item["id"]: item
            for item in manifest["exports"]
        }
        self.assertEqual(
            set(exports),
            {"web.search", "web.read", "web.read_many"},
        )
        self.assertEqual(
            {
                capability_id: item["bind"]["action"]
                for capability_id, item in exports.items()
            },
            {
                "web.search": "search",
                "web.read": "scrape",
                "web.read_many": "scrape_many",
            },
        )

        expected_required = {
            "web.search": ["query"],
            "web.read": ["url"],
            "web.read_many": ["urls"],
        }
        for capability_id, item in exports.items():
            with self.subTest(capability_id=capability_id):
                self.assertEqual(item["name"], capability_id)
                self.assertEqual(item["version"], "1.0")
                self.assertEqual(item["kind"], "TOOL")
                self.assertEqual(item["execution_mode"], "ONE_SHOT")
                self.assertEqual(item["idempotency"], "UNKNOWN")
                self.assertEqual(
                    item["effects"],
                    ["READ", "EXTERNAL_SIDE_EFFECT"],
                )
                self.assertEqual(item["base_risk"], "MEDIUM")
                self.assertEqual(item["required_scopes"], [])
                self.assertEqual(item["required_permissions"], [])
                self.assertEqual(item["danger_patterns"], [])
                schema = item["input_schema"]
                self.assertIs(schema["additionalProperties"], False)
                self.assertEqual(
                    schema["required"],
                    expected_required[capability_id],
                )
                properties = schema["properties"]
                self.assertNotIn("action", properties)
                self.assertNotIn("captcha_api_key", properties)
                self.assertNotIn("output_format", properties)
                self.assertEqual(item["output_schema"]["type"], "object")
                self.assertTrue(
                    {
                        "ok",
                        "tool",
                        "action",
                        "data",
                        "error",
                        "meta",
                    }.issubset(item["output_schema"]["required"])
                )

        # Root physical compatibility remains direct-only, not a V2 authority.
        self.assertEqual(
            TOOL_METADATA["parameters"]["properties"]["action"]["enum"],
            ["search", "scrape", "scrape_many"],
        )
        self.assertEqual(
            TOOL_METADATA["parameters"]["required"],
            ["action"],
        )

    async def test_metadata_identity_and_validation_bounds(self):
        self.assertEqual(TOOL_METADATA["name"], "web_tool")

        for query in ("", "x" * (MAX_QUERY_CHARS + 1)):
            result = await self.tool.search(query)
            self.assert_error(result, "search", "INVALID_ARGUMENT")

        for value in (0, True, SEARCH_RESULTS_MAX + 1):
            result = await self.tool.search("x", max_results=value)
            self.assert_error(result, "search", "INVALID_ARGUMENT")

        result = await self.tool.scrape(
            "https://example.com/",
            timeout=0,
        )
        self.assert_error(result, "scrape", "INVALID_ARGUMENT")
        result = await self.tool.scrape(
            "https://example.com/",
            timeout=READ_TIMEOUT_MAX + 1,
        )
        self.assert_error(result, "scrape", "INVALID_ARGUMENT")
        result = await self.tool.scrape(
            "https://example.com/",
            max_chars=MAX_CONTENT_CHARS_MAX + 1,
        )
        self.assert_error(result, "scrape", "INVALID_ARGUMENT")

    async def test_legacy_format_and_captcha_options_fail_closed(self):
        result = await self.tool.scrape(
            "https://example.com/",
            output_format="json",
        )
        self.assert_error(result, "scrape", "INVALID_ARGUMENT")

        result = await self.tool.scrape(
            "https://example.com/",
            captcha_api_key="secret",
        )
        self.assert_error(result, "scrape", "INVALID_ARGUMENT")
        self.assertNotIn("secret", json.dumps(result))

        with self.assertRaises(WebToolError):
            CapSolverHandler("secret")

    async def test_structured_search_toolresult(self):
        html = b"""
        <div class="result">
          <a class="result__a" href="https://example.com/a">A title</a>
          <div class="result__snippet">A snippet</div>
        </div>
        """
        self.tool.searcher = WebSearcher(
            network_policy=self.tool.network_policy,
            session_factory=FakeSessionFactory([FakeResponse(body=html)]),
        )
        data = self.assert_ok(
            await self.tool.search(" query ", max_results=2),
            "search",
        )
        self.assertEqual(data["query"], "query")
        self.assertEqual(data["provider"], "duckduckgo_html")
        self.assertEqual(data["returned_count"], 1)
        self.assertEqual(data["results"][0]["url"], "https://example.com/a")

    async def test_structured_static_scrape_provenance_hash_and_truncation(self):
        source = static_source()
        content = "ABCDEFGHIJ"
        with patch.object(
            self.tool.scraper,
            "fetch_static",
            new_callable=AsyncMock,
            return_value=source,
        ), patch(
            "tools.v1.web_tool.core.extract_clean_content",
            return_value=(content, "beautifulsoup"),
        ):
            result = await self.tool.scrape(
                "https://example.com/",
                max_chars=5,
            )
        data = self.assert_ok(result, "scrape")
        self.assertTrue(result["meta"]["truncated"])
        self.assertEqual(data["content"], "ABCDE")
        self.assertEqual(data["content_chars"], 10)
        self.assertEqual(data["returned_chars"], 5)
        self.assertEqual(
            data["content_sha256"],
            hashlib.sha256(content.encode()).hexdigest(),
        )
        self.assertEqual(data["method"], "static")
        self.assertEqual(data["provenance"]["extractor"], "beautifulsoup")
        self.assertFalse(data["provenance"]["proxy_used"])
        self.assertNotIn("proxy", data["provenance"].keys() - {"proxy_used"})

    async def test_dynamic_fallback_replaces_short_static_content(self):
        static = static_source()
        dynamic = static_source(rendered=True)
        dynamic["browser_title"] = "JS App"
        dynamic["wait_selector_satisfied"] = None
        with patch.object(
            self.tool.scraper,
            "fetch_static",
            new_callable=AsyncMock,
            return_value=static,
        ), patch.object(
            self.tool.scraper,
            "fetch_dynamic",
            new_callable=AsyncMock,
            return_value=dynamic,
        ), patch(
            "tools.v1.web_tool.core.extract_clean_content",
            side_effect=[
                ("x", "beautifulsoup"),
                ("dynamic long content", "trafilatura"),
            ],
        ):
            self.tool.min_content_length = 5
            result = await self.tool.scrape("https://example.com/")
        data = self.assert_ok(result, "scrape")
        self.assertEqual(data["method"], "dynamic")
        self.assertEqual(data["title"], "JS App")
        self.assertEqual(data["content"], "dynamic long content")

    async def test_raw_extraction_error_is_structured(self):
        with patch.object(
            self.tool.scraper,
            "fetch_static",
            new_callable=AsyncMock,
            return_value=static_source(),
        ), patch(
            "tools.v1.web_tool.core.extract_clean_content",
            side_effect=RuntimeError("extractor internals"),
        ):
            result = await self.tool.scrape(
                "https://example.com/"
            )
        self.assert_error(
            result,
            "scrape",
            "WEB_EXTRACTION_FAILED",
        )
        self.assertEqual(
            result["error"]["details"]["exception_type"],
            "RuntimeError",
        )
        self.assertNotIn(
            "extractor internals",
            json.dumps(result),
        )

    async def test_dynamic_challenge_is_not_downgraded_to_static_warning(self):
        with patch.object(
            self.tool.scraper,
            "fetch_static",
            new_callable=AsyncMock,
            return_value=static_source(),
        ), patch.object(
            self.tool.scraper,
            "fetch_dynamic",
            new_callable=AsyncMock,
            side_effect=WebToolError(
                "WEB_CHALLENGE_REQUIRED",
                "challenge",
            ),
        ), patch(
            "tools.v1.web_tool.core.extract_clean_content",
            return_value=("x", "beautifulsoup"),
        ):
            self.tool.min_content_length = 5
            result = await self.tool.scrape("https://example.com/")
        self.assert_error(
            result,
            "scrape",
            "WEB_CHALLENGE_REQUIRED",
        )

    async def test_static_retry_is_bounded(self):
        success = static_source()
        calls = 0

        async def fake_fetch(**kwargs):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise WebToolError(
                    "WEB_NETWORK_ERROR",
                    "temporary",
                    retryable=True,
                )
            return success

        with patch.object(
            self.tool.scraper,
            "fetch_static",
            side_effect=fake_fetch,
        ), patch(
            "tools.v1.web_tool.core.extract_clean_content",
            return_value=("enough", "beautifulsoup"),
        ), patch(
            "tools.v1.web_tool.core.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await self.tool.scrape(
                "https://example.com/",
                max_retries=MAX_RETRIES,
            )
        self.assert_ok(result, "scrape")
        self.assertEqual(calls, 3)

    async def test_proxy_credentials_never_enter_scrape_result(self):
        secret_proxy = "http://user:password@8.8.8.8:8080"
        with patch.object(
            self.tool,
            "_refresh_proxy_if_needed",
            new_callable=AsyncMock,
            return_value={"http": secret_proxy, "https": secret_proxy},
        ), patch.object(
            self.tool.scraper,
            "fetch_static",
            new_callable=AsyncMock,
            return_value={
                **static_source(),
                "proxy_used": True,
            },
        ), patch(
            "tools.v1.web_tool.core.extract_clean_content",
            return_value=("content", "beautifulsoup"),
        ):
            result = await self.tool.scrape("https://example.com/")
        self.assert_ok(result, "scrape")
        rendered = json.dumps(result)
        self.assertNotIn("password", rendered)
        self.assertNotIn("user:", rendered)
        self.assertTrue(result["data"]["provenance"]["proxy_used"])

    async def test_async_context_does_not_mask_programmer_exception(self):
        tool = WebTool(
            network_policy=make_policy(),
        )
        with patch.object(
            tool,
            "close",
            new_callable=AsyncMock,
            side_effect=WebToolError(
                "WEB_CLEANUP_FAILED",
                "cleanup",
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "programmer error",
            ):
                async with tool:
                    raise RuntimeError("programmer error")

    async def test_execute_aliases_and_invalid_action(self):
        with patch.object(
            self.tool,
            "scrape",
            new_callable=AsyncMock,
            return_value=self.tool._success("scrape", {"x": 1}),
        ) as mocked:
            result = await self.tool.execute(
                action="read",
                url="https://example.com/",
            )
        self.assert_ok(result, "scrape")
        mocked.assert_awaited_once()

        result = await self.tool.execute(action="bad")
        self.assert_error(result, "bad", "INVALID_ARGUMENT")

    async def test_batch_validates_timeout_before_any_dns_preflight(self):
        resolver = FakeResolver()
        tool = WebTool(
            network_policy=NetworkPolicy(resolver),
        )
        try:
            result = await tool.scrape_many(
                ["https://example.com/"],
                timeout=0,
            )
            self.assert_error(
                result,
                "scrape_many",
                "INVALID_ARGUMENT",
            )
            self.assertEqual(resolver.calls, [])
        finally:
            await tool.close()

    async def test_batch_preflight_rejects_invalid_url_before_scrape(self):
        with patch.object(
            self.tool,
            "scrape",
            new_callable=AsyncMock,
        ) as mocked:
            result = await self.tool.scrape_many(
                ["https://example.com/", "http://127.0.0.1/"]
            )
        self.assert_error(result, "scrape_many", "WEB_URL_BLOCKED")
        mocked.assert_not_awaited()

    async def test_batch_is_bounded_preserves_order_and_partial_failure(self):
        tool = WebTool(
            network_policy=make_policy(),
            max_concurrency=2,
        )
        active = 0
        max_active = 0

        async def fake_scrape(url, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(0)
                if url.endswith("/2"):
                    return tool._failure(
                        "scrape",
                        WebToolError("WEB_HTTP_ERROR", "bad"),
                    )
                return tool._success("scrape", {"url": url})
            finally:
                active -= 1

        urls = [
            "https://example.com/1",
            "https://example.com/2",
            "https://example.com/3",
        ]
        try:
            with patch.object(tool, "scrape", side_effect=fake_scrape):
                result = await tool.scrape_many(urls)
            data = self.assert_ok(result, "scrape_many")
            self.assertLessEqual(max_active, 2)
            self.assertEqual(data["requested_count"], 3)
            self.assertEqual(data["succeeded_count"], 2)
            self.assertEqual(data["failed_count"], 1)
            self.assertEqual(
                data["results"][0]["data"]["url"],
                "https://example.com/1",
            )
            self.assertEqual(data["results"][1]["ok"], False)
            self.assertEqual(
                data["results"][2]["data"]["url"],
                "https://example.com/3",
            )
        finally:
            await tool.close()

    async def test_batch_size_hard_limit(self):
        result = await self.tool.scrape_many(
            ["https://example.com/"] * (MAX_BATCH_URLS + 1)
        )
        self.assert_error(result, "scrape_many", "INVALID_ARGUMENT")

    async def test_batch_programmer_error_propagates_without_queue_deadlock(self):
        tool = WebTool(
            network_policy=make_policy(),
            max_concurrency=2,
        )

        async def broken_scrape(url, **kwargs):
            if url.endswith("/1"):
                raise RuntimeError("programmer error")
            await asyncio.sleep(0)
            return tool._success("scrape", {"url": url})

        try:
            with patch.object(tool, "scrape", side_effect=broken_scrape):
                with self.assertRaises(RuntimeError):
                    await asyncio.wait_for(
                        tool.scrape_many(
                            [
                                "https://example.com/1",
                                "https://example.com/2",
                                "https://example.com/3",
                            ]
                        ),
                        timeout=1,
                    )
        finally:
            await tool.close()

    async def test_batch_cancellation_drains_workers(self):
        tool = WebTool(
            network_policy=make_policy(),
            max_concurrency=2,
        )
        started = asyncio.Event()
        stopped = 0

        async def slow_scrape(url, **kwargs):
            nonlocal stopped
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped += 1

        try:
            with patch.object(tool, "scrape", side_effect=slow_scrape):
                task = asyncio.create_task(
                    tool.scrape_many(
                        [
                            "https://example.com/1",
                            "https://example.com/2",
                            "https://example.com/3",
                        ]
                    )
                )
                await started.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertGreaterEqual(stopped, 1)
        finally:
            await tool.close()


class TestExtractors(unittest.IsolatedAsyncioTestCase):
    async def test_structured_extractor_never_calls_page_content(self):
        page = SimpleNamespace(
            content=AsyncMock(side_effect=AssertionError("must not be called")),
            evaluate=AsyncMock(return_value=[]),
        )
        data = await extract_tables_and_charts(
            "<table><tr><td>A</td></tr></table>",
            page_obj=page,
        )
        self.assertEqual(data["tables"], [[["A"]]])
        page.content.assert_not_awaited()

    async def test_table_hard_limit_fails(self):
        rows = "".join("<tr><td>x</td></tr>" for _ in range(MAX_TABLE_ROWS + 1))
        with self.assertRaises(WebToolError) as ctx:
            await extract_tables_and_charts(f"<table>{rows}</table>")
        self.assertEqual(ctx.exception.code, "WEB_STRUCTURED_DATA_LIMIT")


class TestRunEntrypoint(unittest.TestCase):
    def test_sync_run_owns_and_closes_one_lifecycle(self):
        expected = {
            "ok": True,
            "tool": "web_tool",
            "action": "search",
            "data": {"x": 1},
            "error": None,
            "meta": {
                "version": WEB_TOOL_VERSION,
                "truncated": False,
                "warnings": [],
            },
        }
        with patch.object(
            WebTool,
            "execute",
            new_callable=AsyncMock,
            return_value=expected,
        ) as execute, patch.object(
            WebTool,
            "close",
            new_callable=AsyncMock,
        ) as close:
            result = run("search", query="hello")
        self.assertEqual(result, expected)
        execute.assert_awaited_once_with(action="search", query="hello")
        close.assert_awaited_once()


    def test_sync_run_closes_on_programmer_error(self):
        with patch.object(
            WebTool,
            "execute",
            new_callable=AsyncMock,
            side_effect=RuntimeError("programmer error"),
        ), patch.object(
            WebTool,
            "close",
            new_callable=AsyncMock,
        ) as close:
            with self.assertRaisesRegex(
                RuntimeError,
                "programmer error",
            ):
                run("search", query="hello")
        close.assert_awaited_once()


class TestRunActiveLoop(unittest.IsolatedAsyncioTestCase):
    async def test_active_loop_run_returns_task(self):
        expected = {
            "ok": True,
            "tool": "web_tool",
            "action": "search",
            "data": {"x": 1},
            "error": None,
            "meta": {
                "version": WEB_TOOL_VERSION,
                "truncated": False,
                "warnings": [],
            },
        }
        with patch.object(
            WebTool,
            "execute",
            new_callable=AsyncMock,
            return_value=expected,
        ), patch.object(
            WebTool,
            "close",
            new_callable=AsyncMock,
        ):
            value = run("search", query="hello")
            self.assertIsInstance(value, asyncio.Task)
            self.assertEqual(await value, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
