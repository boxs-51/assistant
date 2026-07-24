from core.dynamic_loader import DynamicRegistry
from core.hitl_manager import HITLManager
from core.agent_engine import AgentEngine
from ui.rich_window import AgentUI
from core.gateway_client import GatewayLLMClient

def main():
    # 1. Khởi tạo Registry & nạp mô-đun động
    registry = DynamicRegistry()
    registry.load_all()

    # 2. Khởi tạo bộ quản lý Human-In-The-Loop
    hitl = HITLManager()

    # 3 .
    gateway_client = GatewayLLMClient("http://localhost:8000")

    # 4. Khởi tạo Engine chính
    engine = AgentEngine(registry=registry, hitl=hitl, gateway_client=gateway_client)

    # 5. Mở giao diện ứng dụng
    app = AgentUI(engine=engine, hitl=hitl)
    app.mainloop()

if __name__ == "__main__":
    main()