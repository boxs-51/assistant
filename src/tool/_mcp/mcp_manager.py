import asyncio
import os
from typing import Dict, Any, List, Optional
from .connection import McpConnection, ConnectionStatus
from .factory import McpTransportFactory

from mcp import ClientSession
from mcp.client.stdio import stdio_client

class GatewayMcpManager:
    """
    [TÁI CẤU TRÚC] Chỉ chịu trách nhiệm quản lý Kết nối, Trạng thái,
    Tự động kết nối lại (Reconnect) và Quản lý bộ nhớ đệm Tool (Mục 7, 9, 10).
    """
    def __init__(self, max_retries: int = 5, backoff_factor: float = 2.0):
        self._connections: Dict[str, McpConnection] = {}
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self._health_check_task: Optional[asyncio.Task] = None

    async def register_and_connect(self, server_name: str, command: str, args: List[str] = None):
        """Đăng ký một MCP Server vào Pool và kích hoạt tiến trình kết nối ngầm."""
        args = args or []
        conn = McpConnection(server_name, command, args)
        self._connections[server_name] = conn
        
        # Khởi chạy luồng quản lý vòng đời độc lập cho Connection này
        asyncio.create_task(self._lifecycle_manager(conn))

        # Đợi đồng bộ cho đến khi kết nối thành công hoặc hết lượt đợi ban đầu
        for _ in range(10):
            if conn.status == ConnectionStatus.CONNECTED:
                break
            await asyncio.sleep(0.5)

    async def start_health_checker(self):
        """Kích hoạt Heartbeat giám sát định kỳ trạng thái của các Server (Mục 9)."""
        if self._health_check_task and not self._health_check_task.done():
            return

        async def _check_loop():
            while True:
                await asyncio.sleep(30)  # Chu kỳ 30 giây kiểm tra 1 lần
                for conn in self._connections.values():
                    if conn.status == ConnectionStatus.CONNECTED:
                        try:
                            # Gửi lệnh rỗng hoặc ping thử nghiệm thông qua list_tools để kiểm tra kết nối sống
                            await conn.session.list_tools()
                        except Exception:
                            print(f"💔 [MCP HEALTH] Phát hiện đứt kết nối ngầm tới server: {conn.server_name}")
                            conn.status = ConnectionStatus.FAULTED
                            conn.invalidate_cache()

        self._health_check_task = asyncio.create_task(_check_loop())

    async def _lifecycle_manager(self, conn: McpConnection):
        """Quản lý việc kết nối và tự động Reconnect với Exponential Backoff (Mục 9)."""
        server_params = McpTransportFactory.create_stdio_params(conn.command, conn.args)

        while True:
            if conn.status in [ConnectionStatus.DISCONNECTED, ConnectionStatus.FAULTED]:
                conn.status = ConnectionStatus.CONNECTING
                print(f"🔄 [MCP POOL] Đang thử kết nối tới '{conn.server_name}' (Lần thử: {conn.retry_count})...")
                
                try:
                    # Tạo context cục bộ chạy tiến trình Stdio Client
                    async with stdio_client(server_params) as (read_stream, write_stream):
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            
                            # Cập nhật trạng thái khi kết nối thành công
                            conn.session = session
                            conn.status = ConnectionStatus.CONNECTED
                            conn.retry_count = 0
                            conn.last_error = None
                            print(f"🟢 [MCP POOL] Đã thiết lập kết nối ổn định tới: '{conn.server_name}'")
                            
                            # Tự động nạp và cache tool ngay khi vừa kết nối xong (Mục 10)
                            await self._refresh_tool_cache(conn)

                            # Giữ vòng lặp chạy ngầm để duy trì block context này luôn sống
                            while conn.status == ConnectionStatus.CONNECTED:
                                await asyncio.sleep(1)
                                
                except Exception as e:
                    conn.status = ConnectionStatus.DISCONNECTED
                    conn.last_error = str(e)
                    conn.retry_count += 1
                    
                    # Tính toán thời gian đợi tăng dần (Exponential Backoff)
                    delay = min(self.backoff_factor ** conn.retry_count, 60)
                    print(f"🔴 [MCP POOL LỖI] Kết nối tới '{conn.server_name}' thất bại: {e}. Thử lại sau {delay}s...")
                    await asyncio.sleep(delay)
            else:
                # Nếu trạng thái đang CONNECTED hoặc CONNECTING từ luồng khác, tạm nghỉ để vòng lặp không bị block
                await asyncio.sleep(1)

    async def _refresh_tool_cache(self, conn: McpConnection):
        """Triển khai RPC list_tools ngầm một lần duy nhất để lưu vào Cache (Mục 10)."""
        try:
            mcp_tools_result = await conn.session.list_tools()
            conn.cached_tools = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema
                }
                for tool in mcp_tools_result.tools
            ]
            conn.is_cache_valid = True
            print(f"💾 [MCP CACHE] Đã đồng bộ {len(conn.cached_tools)} công cụ từ server '{conn.server_name}' vào bộ nhớ đệm.")
        except Exception as e:
            print(f"⚠️ [MCP CACHE LỖI] Không thể đọc danh sách tool để lưu cache: {e}")
            conn.invalidate_cache()

    async def get_tools_from_cache(self, server_name: str) -> List[Dict[str, Any]]:
        """Lấy danh sách tool từ bộ nhớ đệm mà không tốn chi phí gọi mạng RPC (Mục 10)."""
        conn = self._connections.get(server_name)
        if not conn or conn.status != ConnectionStatus.CONNECTED:
            return []
            
        if not conn.is_cache_valid:
            await self._refresh_tool_cache(conn)
            
        return conn.cached_tools

    async def get_all_active_servers(self) -> List[str]:
        """Lấy danh sách các Server đang ở trạng thái sẵn sàng làm việc."""
        return [name for name, conn in self._connections.items() if conn.status == ConnectionStatus.CONNECTED]

    def get_raw_session(self, server_name: str) -> Optional[ClientSession]:
        """Cung cấp raw session cho Executor gọi lệnh vật lý (Tách biệt logic thực thi)."""
        conn = self._connections.get(server_name)
        if conn and conn.status == ConnectionStatus.CONNECTED:
            return conn.session
        return None