from __future__ import annotations


class WebToolStealth:
    @staticmethod
    async def apply_stealth_scripts(page) -> None:
        script = """
        () => {
            try {
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['vi-VN', 'vi', 'en-US', 'en']
                });
            } catch (_) {}
        }
        """
        await page.add_init_script(script)

    @staticmethod
    def is_captcha_or_cf_present(html_content: str) -> bool:
        if not isinstance(html_content, str):
            return False
        indicators = (
            "cf-turnstile",
            "g-recaptcha",
            "h-captcha",
            "just a moment",
            "verify you are human",
            "xác minh bạn là con người",
            "checking your browser",
        )
        lowered = html_content.lower()
        return any(value in lowered for value in indicators)
