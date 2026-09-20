import os
import webview

from .ui.bridge import UIBridge
from .loader.registry import DynamicRegistry
from .hitl.hitl_manager import HITLManager
from .core.agent_engine import AgentEngine
from .core.gateway_client import GatewayLLMClient
from .core.client_runtime import ClientRuntime

def main():
    registry = DynamicRegistry(
        tools_dir="tools/v1",
        config_dir="cl/config",
    )
    client_runtime = None

    try:
        registry.load_all()

        hitl=HITLManager()
        client_runtime = ClientRuntime(
            "http://localhost:8000",
            registry,
            hitl=hitl,
        )

        engine = AgentEngine(
            registry=registry,
            hitl=hitl,
            gateway_client=client_runtime.gateway,
            mock_mode=False,
        )
        api = UIBridge(engine=engine, hitl=hitl, client_runtime=client_runtime)
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
    finally:
        try:
            if client_runtime is not None:
                client_runtime.stop()
        finally:
            registry.shutdown()

if __name__ == "__main__":
    main()
