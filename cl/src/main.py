import os
import webview

from .ui.bridge import UIBridge
from .loader.registry import DynamicRegistry
from .hitl.hitl_manager import HITLManager
from .core.agent_engine import AgentEngine
from .core.gateway_client import GatewayLLMClient

def main():
    # 1. Khởi tạo Registry & nạp mô-đun động
    registry = DynamicRegistry(tools_dir="tools/v1",
                               config_dir="cl/config")
    registry.load_all()

    # 2. Khởi tạo bộ quản lý Human-In-The-Loop
    hitl = HITLManager()

    # 3 .
    gateway_client = GatewayLLMClient("http://localhost:8000")

    # 4. Khởi tạo Engine chính
    engine = AgentEngine(registry=registry, hitl=hitl, gateway_client=gateway_client,mock_mode=True)

    api = UIBridge(engine=engine, hitl=hitl)
    html_path = os.path.join(os.path.dirname(__file__), "ui", "web", "index.html")
    # 5. Mở giao diện ứng dụng
    window = webview.create_window(
            title="Modular Agent - Dynamic Risk HITL System",
            url=html_path,
            js_api=api,
            width=1000,
            height=750,
            resizable=True
        )
    api.set_window(window)
    webview.start(debug=True)

if __name__ == "__main__":
    main()