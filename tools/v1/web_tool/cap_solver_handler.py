import time
import requests
from typing import Optional, Dict, Any

class CapSolverHandler:
    """Tích hợp CapSolver API để giải tự động Turnstile và reCAPTCHA."""
    
    BASE_URL = "https://api.capsolver.com"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _create_task(self, task_payload: Dict[str, Any]) -> Optional[str]:
        """Tạo task trên CapSolver và trả về task_id."""
        url = f"{self.BASE_URL}/createTask"
        payload = {
            "clientKey": self.api_key,
            "task": task_payload
        }
        try:
            res = requests.post(url, json=payload, timeout=10).json()
            if res.get("errorId") == 0:
                return res.get("taskId")
            print(f"[CapSolver Error]: {res.get('errorDescription')}")
        except Exception as e:
            print(f"[CapSolver Request Error]: {str(e)}")
        return None

    def _get_result(self, task_id: str, max_wait: int = 60) -> Optional[Dict[str, Any]]:
        """Lấy kết quả giải mã từ CapSolver qua cơ chế Polling."""
        url = f"{self.BASE_URL}/getTaskResult"
        payload = {"clientKey": self.api_key, "taskId": task_id}
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                res = requests.post(url, json=payload, timeout=10).json()
                status = res.get("status")
                if status == "ready":
                    return res.get("solution")
                elif status == "failed":
                    print(f"[CapSolver Failed]: {res.get('errorDescription')}")
                    return None
            except Exception:
                pass
            time.sleep(2)
        return None

    def solve_turnstile(self, website_url: str, sitekey: str) -> Optional[str]:
        """Giải Cloudflare Turnstile."""
        task_payload = {
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": website_url,
            "websiteKey": sitekey,
        }
        task_id = self._create_task(task_payload)
        if task_id:
            solution = self._get_result(task_id)
            return solution.get("token") if solution else None
        return None

    def solve_recaptcha_v2(self, website_url: str, sitekey: str) -> Optional[str]:
        """Giải Google reCAPTCHA v2."""
        task_payload = {
            "type": "ReCaptchaV2TaskProxyLess",
            "websiteURL": website_url,
            "websiteKey": sitekey,
        }
        task_id = self._create_task(task_payload)
        if task_id:
            solution = self._get_result(task_id)
            return solution.get("gRecaptchaResponse") if solution else None
        return None

    def solve_recaptcha_v3(self, website_url: str, sitekey: str, page_action: str = "submit", min_score: float = 0.7) -> Optional[str]:
        """Giải Google reCAPTCHA v3."""
        task_payload = {
            "type": "ReCaptchaV3TaskProxyLess",
            "websiteURL": website_url,
            "websiteKey": sitekey,
            "pageAction": page_action,
            "minScore": min_score
        }
        task_id = self._create_task(task_payload)
        if task_id:
            solution = self._get_result(task_id)
            return solution.get("gRecaptchaResponse") if solution else None
        return None