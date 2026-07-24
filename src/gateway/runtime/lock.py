import asyncio
import time
from typing import Optional
from gateway.storage.drivers.redis.driver import RedisDriver  # Tái sử dụng Driver từ Storage Framework

class DistributedSessionLock:
    """
    Bảo vệ Session Kernel khỏi hiện tượng 'Split-Brain' khi mở rộng Multi-instance.
    Sử dụng thuật toán đơn giản dựa trên Redis SETNX.
    """
    def __init__(self, redis_driver: RedisDriver, session_id: str, ttl_seconds: int = 30):
        self.redis = redis_driver.client  # Truy cập trực tiếp instance Redis client cục bộ
        self.lock_key = f"lock:session:{session_id}"
        self.ttl = ttl_seconds
        self.token = f"node_token_{time.time()}"

    async def acquire(self, timeout_seconds: float = 5.0) -> bool:
        """Cố gắng chiếm quyền giữ Lock trong một khoảng thời gian chờ."""
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            # Thao tác nguyên tử: Chỉ SET nếu chưa tồn tại key (NX) đi kèm thời gian hết hạn (EX)
            acquired = await self.redis.set(self.lock_key, self.token, ex=self.ttl, nx=True)
            if acquired:
                return True
            await asyncio.sleep(0.1)  # Tránh gây nghẽn CPU (Spin-lock mitigation)
        return False

    async def extend(self) -> bool:
        """Gia hạn thời gian sống của Lock (Dùng cho các Session chạy task tự động kéo dài)."""
        # Sử dụng Lua Script để đảm bảo tính nguyên tử (Chỉ gia hạn nếu đúng Token của Node này giữ)
        lua_script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('expire', KEYS[1], ARGV[2])
        else
            return 0
        end
        """
        result = await self.redis.eval(lua_script, 1, self.lock_key, self.token, self.ttl)
        return bool(result)

    async def release(self):
        """Giải phóng khóa an toàn."""
        lua_script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        else
            return 0
        end
        """
        await self.redis.eval(lua_script, 1, self.lock_key, self.token)