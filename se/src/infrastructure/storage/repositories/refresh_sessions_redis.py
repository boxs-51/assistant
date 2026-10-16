"""Redis-primary V2 refresh-session authority.

Each state transition is a single Redis Lua operation. This guarantees atomic
primary ordering, NOT Redis replication/failover/restore durability.
"""
import json
import secrets
from collections.abc import Callable

from ..drivers.redis.driver import RedisDriver
from ..interfaces.refresh_sessions import RefreshLease, RefreshSessionStore


_ISSUE = """
local value = cjson.encode({user=ARGV[1], jti=ARGV[2], epoch=0, state='ACTIVE', lease='', deadline=0})
if redis.call('SET', KEYS[1], value, 'EX', tonumber(ARGV[3]), 'NX') then
  return 1
end
return 0
"""

_BEGIN = """
local raw = redis.call('GET', KEYS[1])
if not raw then return {0} end
local r = cjson.decode(raw)
if r.state ~= 'ACTIVE' or r.user ~= ARGV[1] or r.jti ~= ARGV[2] then return {0} end
local t = redis.call('TIME')
local now = tonumber(t[1])*1000 + math.floor(tonumber(t[2])/1000)
if r.lease ~= '' and tonumber(r.deadline) > now then return {0} end
local ttl = redis.call('PTTL', KEYS[1])
if ttl <= 0 then return {0} end
r.lease = ARGV[3]
r.deadline = now + math.min(tonumber(ARGV[4]), ttl)
redis.call('SET', KEYS[1], cjson.encode(r), 'KEEPTTL')
return {1, tonumber(r.epoch)}
"""

_FINALIZE = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local r = cjson.decode(raw)
local t = redis.call('TIME')
local now = tonumber(t[1])*1000 + math.floor(tonumber(t[2])/1000)
if r.state ~= 'ACTIVE' or r.user ~= ARGV[1] or r.jti ~= ARGV[2]
   or r.lease ~= ARGV[3] or tonumber(r.epoch) ~= tonumber(ARGV[4])
   or tonumber(r.deadline) <= now then return 0 end
r.lease = ''
r.deadline = 0
redis.call('SET', KEYS[1], cjson.encode(r), 'KEEPTTL')
return 1
"""

_ABORT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local r = cjson.decode(raw)
if r.state ~= 'ACTIVE' or r.user ~= ARGV[1] or r.jti ~= ARGV[2]
  or r.lease ~= ARGV[3] or tonumber(r.epoch) ~= tonumber(ARGV[4]) then return 0 end
r.lease = ''
r.deadline = 0
redis.call('SET', KEYS[1], cjson.encode(r), 'KEEPTTL')
return 1
"""

_REVOKE = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local r = cjson.decode(raw)
if r.state ~= 'ACTIVE' or r.user ~= ARGV[1] or r.jti ~= ARGV[2] then return 0 end
r.state = 'REVOKED'
r.epoch = tonumber(r.epoch) + 1
r.lease = ''
r.deadline = 0
redis.call('SET', KEYS[1], cjson.encode(r), 'KEEPTTL')
return 1
"""


class RedisRefreshSessionStore(RefreshSessionStore):
    PREFIX = "session:refresh_v2:"

    def __init__(
        self,
        driver: RedisDriver,
        *,
        is_generation_active: Callable[[], bool] | None = None,
        lease_seconds: int = 10,
    ) -> None:
        if not isinstance(driver, RedisDriver) or lease_seconds <= 0:
            raise ValueError("Redis refresh store requires a RedisDriver and positive lease")
        self._driver = driver
        self._closed = False
        self._is_generation_active = is_generation_active or (lambda: True)
        self._lease_ms = lease_seconds * 1000

    def _check_active(self) -> None:
        if self._closed or not self._is_generation_active():
            raise RuntimeError("Refresh-session store generation is unavailable")

    def _key(self, token_hash: str) -> str:
        if not token_hash:
            raise ValueError("Invalid refresh token hash")
        return self.PREFIX + token_hash

    async def _exec(self, script: str, token_hash: str, args: list) -> object:
        self._check_active()
        return await self._driver.execute_script(script, [self._key(token_hash)], args)

    @staticmethod
    def _receipt(result: object) -> bool:
        if type(result) is not int or result not in (0, 1):
            raise RuntimeError("Unconfirmed Redis refresh-session receipt")
        return result == 1

    async def issue(self, token_hash: str, user_id: str, jti: str, ttl_seconds: int) -> bool:
        if not user_id or not jti or ttl_seconds <= 0:
            raise ValueError("Invalid issuance parameters")
        return self._receipt(await self._exec(_ISSUE, token_hash, [user_id, jti, ttl_seconds]))

    async def begin(self, token_hash: str, user_id: str, jti: str) -> RefreshLease | None:
        lease_id = secrets.token_urlsafe(24)
        result = await self._exec(_BEGIN, token_hash, [user_id, jti, lease_id, self._lease_ms])
        if not isinstance(result, (list, tuple)) or not result or type(result[0]) is not int:
            raise RuntimeError("Unconfirmed Redis begin receipt")
        if result == [0] or result == (0,):
            return None
        if len(result) != 2 or result[0] != 1 or type(result[1]) is not int or result[1] < 0:
            raise RuntimeError("Malformed Redis begin receipt")
        return RefreshLease(token_hash, user_id, jti, lease_id, result[1])

    async def finalize(self, lease: RefreshLease) -> bool:
        return self._receipt(await self._exec(
            _FINALIZE, lease.token_hash,
            [lease.user_id, lease.jti, lease.lease_id, lease.epoch],
        ))

    async def abort(self, lease: RefreshLease) -> None:
        self._receipt(await self._exec(
            _ABORT, lease.token_hash,
            [lease.user_id, lease.jti, lease.lease_id, lease.epoch],
        ))

    async def revoke(self, token_hash: str, user_id: str, jti: str) -> bool:
        return self._receipt(await self._exec(_REVOKE, token_hash, [user_id, jti]))

    async def close(self) -> None:
        self._closed = True
