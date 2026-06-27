import redis.asyncio as redis
import time

class TokenBucketRateLimiter:
    def __init__(self, redis_client: redis.Redis, capacity: int = 100, refill_rate: int = 10):
        self.redis = redis_client
        self.capacity = capacity       # Max requests (RPM)
        self.refill_rate = refill_rate # Requests per second

    async def is_allowed(self, key: str, cost: int = 1) -> tuple[bool, float]:
        """
        Kiểm tra xem một request có được phép hay không dựa trên thuật toán Token Bucket.
        """
        current_time = time.time()
        bucket_key = f"rate_limit:{key}"
        
        # Lấy thông tin bucket từ Redis trong một transaction
        pipe = self.redis.pipeline()
        pipe.hget(bucket_key, "tokens")
        pipe.hget(bucket_key, "last_refill")
        results = await pipe.execute()
        
        tokens_str, last_refill_str = results
        
        tokens = float(tokens_str) if tokens_str else self.capacity
        last_refill = float(last_refill_str) if last_refill_str else current_time

        # Nạp lại token dựa trên thời gian đã trôi qua
        time_passed = current_time - last_refill
        new_tokens = time_passed * self.refill_rate
        tokens = min(self.capacity, tokens + new_tokens)
        
        if tokens >= cost:
            # Nếu đủ token, trừ đi và cập nhật bucket
            new_tokens_count = tokens - cost
            await self.redis.hmset(bucket_key, {"tokens": new_tokens_count, "last_refill": current_time})
            return True, 0.0
        else:
            # Nếu không đủ, tính toán thời gian phải chờ
            tokens_needed = cost - tokens
            wait_time = tokens_needed / self.refill_rate
            return False, wait_time
