"""AE359 R2: real standalone Redis primary, two clients and OS processes.

Never a failover/restore durability certificate. A dedicated disposable local Redis
service is mandatory. These tests contain synthetic hashes, users and JTIs only.
Do not connect to production or reuse a shared Redis database.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.drivers.redis.driver import RedisDriver
from se.src.infrastructure.storage.repositories.refresh_sessions_redis import RedisRefreshSessionStore


pytestmark = pytest.mark.skipif(
    os.getenv("AE359_REDIS_REAL_CI") != "1",
    reason="Only the dedicated, disposable, real-Redis CI job may execute this module",
)


def _url():
    if os.getenv("AE359_REDIS_REAL_CI") != "1":
        raise RuntimeError("Isolated Redis CI admission is required")
    url = os.environ["AE359_REDIS_REAL_URL"]
    parsed = urlsplit(url)
    if (parsed.scheme != "redis" or parsed.hostname not in ("127.0.0.1", "localhost")
            or parsed.port != 6379 or parsed.path != "/15"
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise RuntimeError("AE359 tests permit only credential-free local Redis DB 15")
    return url


def _token_hash():
    return "ae359-test-only-" + uuid4().hex


async def _driver():
    driver = RedisDriver(DriverConfig(options={"url": _url()}))
    await driver.connect()
    client = driver._require_client()
    assert await client.ping() is True
    assert await client.config_get("databases")  # Physical standalone Redis, not a fake.
    return driver


async def _wait_value(client, key, expected, *, seconds=10):
    until = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < until:
        value = await client.get(key)
        if value == expected:
            return
        await asyncio.sleep(0.025)
    raise AssertionError("Independent-process Redis synchronization timed out")


async def _worker(mode, token_hash, user, jti, ready, go):
    driver = await _driver()
    store = RedisRefreshSessionStore(driver, lease_seconds=2)
    try:
        client = driver._require_client()
        client_id = await client.client_id()
        if mode == "finalize_after_revoke":
            lease = await store.begin(token_hash, user, jti)
            assert lease is not None
            await client.incr(ready)
            await client.expire(ready, 30)
            await _wait_value(client, go, "go")
            outcome = {"acquired": True, "finalized": await store.finalize(lease)}
        else:
            await client.incr(ready)
            await client.expire(ready, 30)
            await _wait_value(client, go, "go")
            if mode == "begin":
                lease = await store.begin(token_hash, user, jti)
                outcome = {"acquired": lease is not None}
            elif mode == "issue":
                outcome = {"issued": await store.issue(token_hash, user, jti, 15)}
            else:
                raise ValueError("Unknown test worker operation")
        print(json.dumps({"pid": os.getpid(), "client_id": client_id, **outcome}), flush=True)
    finally:
        await store.close()
        await driver.disconnect()


async def _launch(mode, token_hash, user, jti, ready, go):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd()) + os.pathsep + env.get("PYTHONPATH", "")
    return await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).resolve()), "--ae359-worker",
        mode, token_hash, user, jti, ready, go,
        cwd=str(Path.cwd()), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )


async def _collect(processes):
    try:
        results = []
        for process in processes:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
            assert process.returncode == 0, (
                "Real Redis subprocess failed: " + stderr.decode(errors="replace")[-1500:]
            )
            results.append(json.loads(stdout.decode().strip().splitlines()[-1]))
        assert len({result["pid"] for result in results}) == len(results)
        assert len({result["client_id"] for result in results}) == len(results)
        assert all(result["pid"] != os.getpid() for result in results)
        return results
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.wait()


def test_real_two_clients_issue_subject_jti_ttl_and_fenced_revoke():
    async def run():
        a, b = await _driver(), await _driver()
        token = _token_hash()
        key = RedisRefreshSessionStore.PREFIX + token
        first = RedisRefreshSessionStore(a)
        second = RedisRefreshSessionStore(b)
        try:
            assert await a._require_client().client_id() != await b._require_client().client_id()
            assert await first.issue(token, "user-synthetic", "jti-synthetic", 15) is True
            assert await second.issue(token, "user-synthetic", "jti-synthetic", 15) is False
            assert await second.begin(token, "wrong-user", "jti-synthetic") is None
            assert await second.begin(token, "user-synthetic", "wrong-jti") is None
            lease = await first.begin(token, "user-synthetic", "jti-synthetic")
            assert lease is not None
            assert await second.begin(token, "user-synthetic", "jti-synthetic") is None
            assert 0 < await b._require_client().pttl(key) <= 15_000
            assert await second.revoke(token, "wrong-user", "jti-synthetic") is False
            assert await second.revoke(token, "user-synthetic", "jti-synthetic") is True
            assert await first.finalize(lease) is False
            assert await second.begin(token, "user-synthetic", "jti-synthetic") is None
            assert await first.issue(token, "user-synthetic", "jti-synthetic", 15) is False
        finally:
            await a._require_client().delete(key)
            await first.close()
            await second.close()
            await a.disconnect()
            await b.disconnect()
    asyncio.run(run())


def test_real_two_os_processes_compete_for_one_begin_lease():
    async def run():
        driver = await _driver()
        store = RedisRefreshSessionStore(driver)
        client = driver._require_client()
        token = _token_hash()
        ready, go = "ae359:r2:ready:" + token, "ae359:r2:go:" + token
        processes = []
        try:
            assert await store.issue(token, "u", "j", 20) is True
            processes = [await _launch("begin", token, "u", "j", ready, go) for _ in range(2)]
            await _wait_value(client, ready, "2")
            await client.set(go, "go", ex=15)
            outcomes = await _collect(processes)
            assert sum(item["acquired"] for item in outcomes) == 1
        finally:
            for process in processes:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            await client.delete(ready, go, RedisRefreshSessionStore.PREFIX + token)
            await store.close()
            await driver.disconnect()
    asyncio.run(run())


def test_real_two_os_processes_issue_nx_at_most_once():
    async def run():
        driver = await _driver()
        client = driver._require_client()
        token = _token_hash()
        ready, go = "ae359:r2:ready:" + token, "ae359:r2:go:" + token
        processes = []
        try:
            processes = [await _launch("issue", token, "u", "j", ready, go) for _ in range(2)]
            await _wait_value(client, ready, "2")
            await client.set(go, "go", ex=15)
            outcomes = await _collect(processes)
            assert sum(item["issued"] for item in outcomes) == 1
        finally:
            for process in processes:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            await client.delete(ready, go, RedisRefreshSessionStore.PREFIX + token)
            await driver.disconnect()
    asyncio.run(run())


def test_real_begin_process_revoke_other_client_then_stale_finalize_denied():
    async def run():
        a, b = await _driver(), await _driver()
        store = RedisRefreshSessionStore(a)
        revoke_store = RedisRefreshSessionStore(b)
        client = a._require_client()
        token = _token_hash()
        ready, go = "ae359:r2:ready:" + token, "ae359:r2:go:" + token
        processes = []
        try:
            assert await store.issue(token, "u", "j", 20)
            processes = [await _launch("finalize_after_revoke", token, "u", "j", ready, go)]
            await _wait_value(client, ready, "1")
            assert await revoke_store.revoke(token, "u", "j") is True
            await client.set(go, "go", ex=15)
            result = (await _collect(processes))[0]
            assert result["acquired"] is True and result["finalized"] is False
            assert await store.begin(token, "u", "j") is None
        finally:
            for process in processes:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            await client.delete(ready, go, RedisRefreshSessionStore.PREFIX + token)
            await store.close()
            await revoke_store.close()
            await a.disconnect()
            await b.disconnect()
    asyncio.run(run())


def test_real_script_flush_noscript_expiry_and_keepttl():
    async def run():
        driver = await _driver()
        client = driver._require_client()
        store = RedisRefreshSessionStore(driver, lease_seconds=1)
        token = _token_hash()
        key = RedisRefreshSessionStore.PREFIX + token
        try:
            assert await client.script_flush() is True
            assert await store.issue(token, "u", "j", 4) is True
            original_ttl = await client.pttl(key)
            await client.script_flush()
            first = await store.begin(token, "u", "j")
            assert first is not None
            assert 0 < await client.pttl(key) <= original_ttl
            await asyncio.sleep(1.12)
            assert await store.finalize(first) is False
            second = await store.begin(token, "u", "j")
            assert second is not None and second.lease_id != first.lease_id
            assert await store.finalize(second) is True
            assert 0 < await client.pttl(key) <= original_ttl
            await asyncio.sleep(3.1)
            assert await store.begin(token, "u", "j") is None
            assert await client.pttl(key) == -2
        finally:
            await client.delete(key)
            await store.close()
            await driver.disconnect()
    asyncio.run(run())


def test_real_disconnected_driver_propagates_not_false_ack():
    async def run():
        driver = await _driver()
        store = RedisRefreshSessionStore(driver)
        await driver.disconnect()
        try:
            with pytest.raises(RuntimeError, match="not connected"):
                await store.revoke(_token_hash(), "u", "j")
            with pytest.raises(RuntimeError, match="not connected"):
                await store.begin(_token_hash(), "u", "j")
        finally:
            await store.close()
    asyncio.run(run())


def test_real_client_pause_timeout_is_uncertain_not_revoke_ack():
    async def run():
        writer, observer = await _driver(), await _driver()
        store = RedisRefreshSessionStore(writer)
        client = observer._require_client()
        token = _token_hash()
        key = RedisRefreshSessionStore.PREFIX + token
        try:
            assert await store.issue(token, "u", "j", 10)
            # Physical command pause, not synthetic failure nor post-execution ACK loss.
            assert await client.execute_command("CLIENT", "PAUSE", "700", "ALL") == "OK"
            with pytest.raises((asyncio.TimeoutError, RedisError)):
                await asyncio.wait_for(store.revoke(token, "u", "j"), timeout=0.05)
            await asyncio.sleep(0.85)
            raw = await client.get(key)
            assert raw is not None
            assert json.loads(raw)["state"] in ("ACTIVE", "REVOKED")
            # A timeout supplies no confirmed revoke ACK, regardless of later state.
        finally:
            await client.delete(key)
            await store.close()
            await writer.disconnect()
            await observer.disconnect()
    asyncio.run(run())


if __name__ == "__main__" and len(sys.argv) == 8 and sys.argv[1] == "--ae359-worker":
    asyncio.run(_worker(*sys.argv[2:]))
