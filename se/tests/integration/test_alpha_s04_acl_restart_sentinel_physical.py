"""S04 Redis 7.4 writer-epoch persistence and automatic failover witnesses.

All Docker objects, ACL identities, credentials and payloads are disposable.
They are NOT production credential, binary, Sentinel HA or rollout proofs.
The negative controls deliberately demonstrate stale/unsaved ACL hazards.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

import pytest
import redis.asyncio as redis
from redis.exceptions import ResponseError


def _strict(which: str) -> None:
    if os.environ.get(which) != "1":
        pytest.fail(f"S04_HARNESS_ERROR: {which} strict physical gate required")


def _docker(*args: str) -> str:
    done = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=40
    )
    if done.returncode:
        pytest.fail(
            "S04_HARNESS_ERROR: disposable Docker command failed "
            + args[0] + " (exit " + str(done.returncode) + ")"
        )
    return done.stdout.strip()


async def _ready(client, label: str, loops: int = 100) -> None:
    for _ in range(loops):
        try:
            if await client.ping():
                return
        except (ConnectionError, OSError, redis.RedisError):
            pass
        await asyncio.sleep(0.1)
    pytest.fail("S04_HARNESS_ERROR: " + label + " did not become healthy")


async def _deny_set(client, key: str) -> None:
    with pytest.raises(ResponseError, match="(?i)permission"):
        await client.set(key, "legacy-rollback", ex=300)


def _acl_user(name: str, pw: str, *, allow_set: bool,
              allow_getdel: bool = False) -> list[str]:
    rules = [
        "reset", "on", ">" + pw,
        "~pending_reg:*", "~pending_password_reset:*",
        "+ping", "+get", "+select",
    ]
    if allow_set:
        rules.append("+set")
    if allow_getdel:
        rules.append("+getdel")
    return ["ACL", "SETUSER", name, *rules]


@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
@pytest.mark.parametrize("persistence", ("saved", "unsaved-control"))
def test_s04_acl_restart_writer_epoch_persistence(purpose, persistence):
    """Saved ACL survives restart; unsaved ACL restores unsafe old authority."""
    _strict("S04_ACL_RESTART_STRICT")

    async def run():
        suffix = uuid4().hex[:12]
        container = "s04-restart-" + suffix
        old_user, modern_user = "s04_old_" + suffix, "s04_new_" + suffix
        old_pw, modern_pw = uuid4().hex, uuid4().hex
        key = ("pending_reg:" if purpose == "registration"
               else "pending_password_reset:") + suffix + "@example.invalid"
        with tempfile.TemporaryDirectory(prefix="s04-restart-") as tmp:
            root = Path(tmp)
            root.chmod(0o777)
            acl = root / "users.acl"
            acl.write_text(
                "user default on nopass ~* &* +@all\n"
                + "user " + old_user + " on >" + old_pw
                + " ~pending_reg:* ~pending_password_reset:*"
                + " +ping +get +select +set\n"
                + "user " + modern_user + " on >" + modern_pw
                + " ~pending_reg:* ~pending_password_reset:*"
                + " +ping +get +select +set +getdel\n",
                encoding="utf-8",
            )
            acl.chmod(0o666)
            try:
                _docker(
                    "run", "-d", "--name", container,
                    "-p", "127.0.0.1:6383:6379",
                    "-v", str(root) + ":/state",
                    "redis:7.4-alpine", "redis-server",
                    "--aclfile", "/state/users.acl",
                    "--save", "", "--appendonly", "no",
                )
                admin = redis.Redis(host="127.0.0.1", port=6383,
                                    decode_responses=True)
                old = redis.Redis(
                    host="127.0.0.1", port=6383, username=old_user,
                    password=old_pw, decode_responses=True,
                )
                modern = redis.Redis(
                    host="127.0.0.1", port=6383, username=modern_user,
                    password=modern_pw, decode_responses=True,
                )
                try:
                    await _ready(admin, "restart Redis7.4")
                    assert str((await admin.info("server"))["redis_version"]).startswith("7.4.")
                    assert await old.set(key, "legacy-live", ex=300) is True
                    assert await admin.get(key) == "legacy-live"
                    assert await admin.execute_command("ACL", "SETUSER",
                                                       old_user, "-set")
                    await _deny_set(old, key)
                    if persistence == "saved":
                        assert await admin.execute_command("ACL", "SAVE") == "OK"
                    assert await admin.set(key, "modern-acknowledged",
                                           ex=300) is True
                finally:
                    await admin.aclose()
                    await old.aclose()
                    await modern.aclose()

                _docker("restart", "--time", "1", container)
                admin = redis.Redis(host="127.0.0.1", port=6383,
                                    decode_responses=True)
                old = redis.Redis(
                    host="127.0.0.1", port=6383, username=old_user,
                    password=old_pw, decode_responses=True,
                )
                modern = redis.Redis(
                    host="127.0.0.1", port=6383, username=modern_user,
                    password=modern_pw, decode_responses=True,
                )
                try:
                    await _ready(admin, "restart Redis7.4 after restart")
                    assert await admin.get(key) is None, (
                        "S04_HARNESS_ERROR: disabled AOF/RDB must not restore an OTP"
                    )
                    # Reissuing after restart tests the current ACL authority,
                    # not Redis payload durability (RDB/AOF deliberately off).
                    assert await modern.set(
                        key, "modern-after-restart", ex=300, nx=True
                    ) is True
                    if persistence == "saved":
                        await _deny_set(old, key)
                        assert await modern.getdel(key) == "modern-after-restart"
                        assert await modern.getdel(key) is None
                    else:
                        # Expected negative control: unsaved revoke vanished,
                        # and old SET is once again authorized.
                        assert await old.set(
                            key, "old-after-unsaved-restart", ex=300
                        ) is True, (
                            "S04_HARNESS_ERROR: unsaved ACL risk absent"
                        )
                        assert await admin.get(key) == "old-after-unsaved-restart"
                finally:
                    await admin.aclose()
                    await old.aclose()
                    await modern.aclose()
            finally:
                subprocess.run(
                    ["docker", "rm", "-f", container],
                    capture_output=True, text=True, timeout=30,
                    check=False,
                )
    asyncio.run(run())


@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
@pytest.mark.parametrize("candidate_acl", ("stale-control", "synced"))
def test_s04_sentinel_automatic_promotion_writer_acl(purpose, candidate_acl):
    """Sentinel must elect a replica; its own ACL governs old SET safety."""
    _strict("S04_SENTINEL_STRICT")

    async def run():
        suffix = uuid4().hex[:12]
        net = "s04-sentinel-net-" + suffix
        master = "s04-sentinel-master-" + suffix
        replica_name = "s04-sentinel-replica-" + suffix
        sentinel_name = "s04-sentinel-monitor-" + suffix
        old_user = "s04old_" + suffix
        modern_user = "s04new_" + suffix
        old_pw, modern_pw = uuid4().hex, uuid4().hex
        key = ("pending_reg:" if purpose == "registration"
               else "pending_password_reset:") + suffix + "@example.invalid"
        primary = redis.Redis(host="127.0.0.1", port=6381,
                              decode_responses=True)
        secondary = redis.Redis(host="127.0.0.1", port=6382,
                                decode_responses=True)
        sentinel = redis.Redis(host="127.0.0.1", port=26379,
                               decode_responses=True)
        created = []
        with tempfile.TemporaryDirectory(prefix="s04-sentinel-") as tmp:
            path = Path(tmp)
            path.chmod(0o777)
            try:
                _docker("network", "create", net)
                created.append(("network", net))
                _docker(
                    "run", "-d", "--name", master,
                    "--network", net,
                    "-p", "127.0.0.1:6381:6379",
                    "redis:7.4-alpine", "redis-server",
                    "--save", "", "--appendonly", "no",
                )
                created.append(("container", master))
                _docker(
                    "run", "-d", "--name", replica_name,
                    "--network", net,
                    "-p", "127.0.0.1:6382:6379",
                    "redis:7.4-alpine", "redis-server",
                    "--replicaof", master, "6379",
                    "--save", "", "--appendonly", "no",
                )
                created.append(("container", replica_name))
                await _ready(primary, "sentinel primary")
                await _ready(secondary, "sentinel replica")
                assert str((await primary.info("server"))["redis_version"]).startswith("7.4.")
                assert str((await secondary.info("server"))["redis_version"]).startswith("7.4.")

                for _ in range(120):
                    ri = await secondary.info("replication")
                    if ri.get("role") == "slave" and ri.get("master_link_status") == "up":
                        break
                    await asyncio.sleep(0.2)
                else:
                    pytest.fail("S04_HARNESS_ERROR: physical replica never synchronized")

                assert await primary.execute_command(
                    *_acl_user(old_user, old_pw, allow_set=True)
                )
                assert await secondary.execute_command(
                    *_acl_user(old_user, old_pw,
                               allow_set=(candidate_acl == "stale-control"))
                )
                assert await secondary.execute_command(
                    *_acl_user(modern_user, modern_pw,
                               allow_set=True, allow_getdel=True)
                )
                expected = "modern-acknowledged-" + suffix
                assert await primary.set(key, expected, ex=300, nx=True) is True
                for _ in range(120):
                    if await secondary.get(key) == expected:
                        break
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail("S04_HARNESS_ERROR: Redis value never replicated")

                ip = _docker(
                    "inspect", "-f",
                    "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                    master,
                )
                assert ip.count(".") == 3, "S04_HARNESS_ERROR: no container IP"
                cfg = path / "sentinel.conf"
                cfg.write_text(
                    "port 26379\nbind 0.0.0.0\nprotected-mode no\n"
                    "sentinel monitor s04master " + ip + " 6379 1\n"
                    "sentinel down-after-milliseconds s04master 1000\n"
                    "sentinel failover-timeout s04master 10000\n"
                    "sentinel parallel-syncs s04master 1\n",
                    encoding="utf-8",
                )
                cfg.chmod(0o666)
                _docker(
                    "run", "-d", "--name", sentinel_name,
                    "--network", net,
                    "-p", "127.0.0.1:26379:26379",
                    "-v", str(path) + ":/sentinel",
                    "redis:7.4-alpine", "redis-server",
                    "/sentinel/sentinel.conf", "--sentinel",
                )
                created.append(("container", sentinel_name))
                await _ready(sentinel, "Sentinel observer")
                before = await sentinel.execute_command(
                    "SENTINEL", "get-master-addr-by-name", "s04master"
                )
                assert before and before[0] == ip, (
                    "S04_HARNESS_ERROR: Sentinel not monitoring original primary"
                )
                # Sentinel, not the test, must perform promotion: stop only
                # the owned ephemeral Docker master; never issue REPLICAOF.
                _docker("stop", "--time", "1", master)
                promoted = False
                for _ in range(240):
                    try:
                        leader = await sentinel.execute_command(
                            "SENTINEL", "get-master-addr-by-name", "s04master"
                        )
                        role = (await secondary.info("replication"))["role"]
                        if (leader and leader[0] != ip and role == "master"):
                            promoted = True
                            break
                    except redis.RedisError:
                        pass
                    await asyncio.sleep(0.25)
                assert promoted, (
                    "S04_HARNESS_ERROR: Sentinel did not automatically "
                    "promote the Redis replica"
                )
                old = redis.Redis(
                    host="127.0.0.1", port=6382,
                    username=old_user, password=old_pw, decode_responses=True,
                )
                modern = redis.Redis(
                    host="127.0.0.1", port=6382,
                    username=modern_user, password=modern_pw,
                    decode_responses=True,
                )
                try:
                    assert await old.ping() and await modern.ping()
                    if candidate_acl == "stale-control":
                        assert await old.set(
                            key, "old-overwrite-after-sentinel", ex=300
                        ) is True, (
                            "S04_HARNESS_ERROR: stale ACL negative control absent"
                        )
                        assert await secondary.get(key) == (
                            "old-overwrite-after-sentinel"
                        )
                    else:
                        await _deny_set(old, key)
                        assert await modern.getdel(key) == expected, (
                            "S04_SENTINEL_SEMANTIC_RED: lost acknowledged OTP"
                        )
                        assert await modern.getdel(key) is None
                        assert await modern.set(
                            key, "next-modern", ex=300, nx=True
                        ) is True
                        await _deny_set(old, key)
                        assert await modern.getdel(key) == "next-modern"
                finally:
                    await old.aclose()
                    await modern.aclose()
            finally:
                await primary.aclose()
                await secondary.aclose()
                await sentinel.aclose()
                for kind, name in reversed(created):
                    subprocess.run(
                        (["docker", "rm", "-f", name] if kind == "container"
                         else ["docker", "network", "rm", name]),
                        capture_output=True, text=True, timeout=30, check=False,
                    )
    asyncio.run(run())
