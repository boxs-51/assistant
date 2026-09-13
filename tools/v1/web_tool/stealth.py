import random
import asyncio
from .cap_solver_handler import CapSolverHandler

class WebToolStealth:
    @staticmethod
    async def apply_stealth_scripts(page):
        """Tiêm script vô hiệu hóa các dấu vết nhận diện Playwright/Automation."""
        stealth_js = """
        () => {
            // Ghi đè thuộc tính navigator.webdriver
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            
            // Giả lập danh sách plugin
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });

            // Giả lập ngôn ngữ chuẩn
            Object.defineProperty(navigator, 'languages', {
                get: () => ['vi-VN', 'vi', 'en-US', 'en'],
            });

            // Ghi đè Chrome runtime
            window.chrome = { runtime: {} };
        }
        """
        await page.add_init_script(stealth_js)

    @staticmethod
    async def simulate_human_behavior(page):
        """Giả lập thao tác chuột và cuộn trang của người dùng thật."""
        try:
            # Di chuyển chuột ngẫu nhiên
            x = random.randint(100, 700)
            y = random.randint(100, 500)
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.3, 0.8))

            # Cuộn nhẹ trang web
            await page.evaluate(f"window.scrollBy(0, {random.randint(200, 500)});")
            await asyncio.sleep(random.uniform(0.5, 1.2))
        except Exception:
            pass

    @staticmethod
    def is_captcha_or_cf_present(html_content: str) -> bool:
        """Kiểm tra xem trang có đang bị kẹt ở màn hình CAPTCHA / Cloudflare hay không."""
        captcha_indicators = [
            "cf-turnstile",
            "g-recaptcha",
            "h-captcha",
            "just a moment...",
            "verify you are human",
            "xác minh bạn là con người",
            "checking your browser",
        ]
        lowered = html_content.lower()
        return any(indicator in lowered for indicator in captcha_indicators)

    @staticmethod
    async def extract_and_solve_captcha(page, url: str, api_key: str) -> bool:
        """Tự động phát hiện loại CAPTCHA, lấy sitekey, giải và nhúng token vào trang."""
        solver = CapSolverHandler(api_key)

        # 1. Kiểm tra và xử lý Cloudflare Turnstile
        turnstile_elem = await page.query_selector("[data-sitekey], .cf-turnstile, iframe[src*='challenges.cloudflare.com']")
        if turnstile_elem:
            sitekey = await page.evaluate("""() => {
                const el = document.querySelector('[data-sitekey]');
                if (el) return el.getAttribute('data-sitekey');
                const iframe = document.querySelector("iframe[src*='challenges.cloudflare.com']");
                if (iframe) {
                    const match = iframe.src.match(/sitekey=([^&]+)/);
                    return match ? match[1] : null;
                }
                return null;
            }""")

            if sitekey:
                print(f"[+] Tìm thấy Turnstile Sitekey: {sitekey}. Đang gửi tới CapSolver...")
                token = solver.solve_turnstile(url, sitekey)
                if token:
                    # Inject token vào DOM và kích hoạt Callback
                    await page.evaluate(f"""(token) => {{
                        const input = document.querySelector('[name="cf-turnstile-response"]') || document.createElement('input');
                        input.value = token;
                        
                        // Kích hoạt callback nếu trang có định nghĩa hàm callback
                        if (window.cfCallback) window.cfCallback(token);
                        if (window.tsCallback) window.tsCallback(token);
                    }}""", token)
                    await asyncio.sleep(2)
                    return True

        # 2. Kiểm tra và xử lý Google reCAPTCHA v2
        recaptcha_elem = await page.query_selector(".g-recaptcha, [data-sitekey], iframe[src*='recaptcha']")
        if recaptcha_elem:
            sitekey = await page.evaluate("""() => {
                const el = document.querySelector('.g-recaptcha[data-sitekey]');
                if (el) return el.getAttribute('data-sitekey');
                const iframe = document.querySelector("iframe[src*='recaptcha']");
                if (iframe) {
                    const match = iframe.src.match(/k=([^&]+)/);
                    return match ? match[1] : null;
                }
                return null;
            }""")

            if sitekey:
                print(f"[+] Tìm thấy reCAPTCHA Sitekey: {sitekey}. Đang gửi tới CapSolver...")
                token = solver.solve_recaptcha_v2(url, sitekey)
                if token:
                    # Inject token vào textarea g-recaptcha-response
                    await page.evaluate(f"""(token) => {{
                        const el = document.getElementById('g-recaptcha-response') || document.querySelector('[name="g-recaptcha-response"]');
                        if (el) el.value = token;
                        
                        // Kích hoạt Submit/Callback nếu có
                        if (typeof ___grecaptcha_cfg !== 'undefined') {{
                            Object.keys(___grecaptcha_cfg.clients).forEach(cid => {{
                                const client = ___grecaptcha_cfg.clients[cid];
                                Object.keys(client).forEach(key => {{
                                    if (client[key] && typeof client[key].callback === 'function') {{
                                        client[key].callback(token);
                                    }}
                                }});
                            }});
                        }}
                    }}""", token)
                    await asyncio.sleep(2)
                    return True
                
        # 3. Kiểm tra và xử lý Google reCAPTCHA v3
        v3_sitekey = await page.evaluate("""() => {
            const script = document.querySelector("script[src*='recaptcha/api.js?render=']");
            if (script) {
                const match = script.src.match(/render=([^&]+)/);
                return match ? match[1] : null;
            }
            return null;
        }""")
        if v3_sitekey:
            print(f"[+] Tìm thấy reCAPTCHA v3 Sitekey: {v3_sitekey}. Đang gửi tới CapSolver...")
            token = solver.solve_recaptcha_v3(url, v3_sitekey)
            if token:
                await page.evaluate(f"""(token) => {{
                    const el = document.getElementById('g-recaptcha-response-v3') || document.querySelector('[name="g-recaptcha-response"]');
                    if (el) el.value = token;
                    if (window.grecaptcha && window.grecaptcha.getResponse) {{
                        window.grecaptcha.getResponse = () => token;
                    }}
                }}""", token)
                await asyncio.sleep(2)
                return True

        return False