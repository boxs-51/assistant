--[[
  Atomic Token Bucket algorithm implementation for Redis.

  KEYS[1]: The key for the token bucket hash (e.g., "rate_limit:client_id").

  ARGV[1]: capacity      - Maximum number of tokens in the bucket.
  ARGV[2]: refill_rate   - Tokens to add per second.
  ARGV[3]: cost          - Number of tokens this request costs.
  ARGV[4]: current_time  - Current server time as a float string.
  ARGV[5]: ttl           - Time-to-live for the key in seconds.

  Returns:
    A table with 3 values:
    1. allowed (integer): 1 if allowed, 0 if denied.
    2. remaining_tokens (float): Number of tokens left after the operation.
    3. wait_time (float): Seconds to wait before the request can be retried.
--]]

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local current_time = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local bucket = redis.call('HGETALL', key)
local last_refill = tonumber(bucket[4]) or current_time
local tokens = tonumber(bucket[2]) or capacity

local time_passed = current_time - last_refill
local new_tokens = time_passed * refill_rate
tokens = math.min(capacity, tokens + new_tokens)

if tokens >= cost then
  local remaining = tokens - cost
  redis.call('HSET', key, 'tokens', remaining, 'last_refill', current_time)
  redis.call('EXPIRE', key, ttl)
  return {1, remaining, 0}
else
  local tokens_needed = cost - tokens
  local wait_time = tokens_needed / refill_rate
  return {0, tokens, wait_time}
end