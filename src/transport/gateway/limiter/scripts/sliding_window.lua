--[[
  Atomic Sliding Window Log algorithm for Redis using a Sorted Set.

  KEYS[1]: The key for the sorted set (e.g., "rate_limit:sw:client_id").

  ARGV[1]: limit         - Maximum number of requests in the window.
  ARGV[2]: window_size   - The duration of the window in seconds.
  ARGV[3]: current_time  - Current server time as a float string.
  ARGV[4]: ttl           - Time-to-live for the key in seconds.

  Returns:
    A table with 2 values:
    1. allowed (integer): 1 if allowed, 0 if denied.
    2. remaining (integer): Number of requests remaining in the window.
--]]

local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_size = tonumber(ARGV[2])
local current_time = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

-- 1. Dọn dẹp các request cũ nằm ngoài cửa sổ thời gian
local window_start = current_time - window_size
redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

-- 2. Đếm số request hiện tại trong cửa sổ
local current_count = redis.call('ZCARD', key)

if current_count < limit then
  -- 3. Thêm request hiện tại vào set. Member là duy nhất để tránh trùng lặp.
  redis.call('ZADD', key, current_time, current_time)
  redis.call('EXPIRE', key, ttl)
  return {1, limit - (current_count + 1)}
else
  return {0, 0}
end