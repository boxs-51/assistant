"""Disposable Redis RESP2 TCP ACK-cut proxy for AE359 physical T1 tests.

All traffic is on 127.0.0.1. A real Redis executes the command and the
proxy captures one complete successful RESP reply without sending its bytes
to the caller. Observer connections must use the backend port directly.
"""
import asyncio


class RespAckCutProxy:
    def __init__(self, upstream_host="127.0.0.1", upstream_port=6379):
        if upstream_host not in ("127.0.0.1", "localhost"):
            raise ValueError("Only disposable loopback Redis is allowed")
        self.upstream_host = upstream_host
        self.upstream_port = int(upstream_port)
        self.server = None
        self.port = None
        self.armed = False
        self.captured = asyncio.Event()
        self.release_drop = asyncio.Event()
        self.upstream_success_frame = False
        self.client_ack_bytes = 0
        self._connections = set()

    @staticmethod
    async def _reply(reader):
        kind = await reader.readexactly(1)
        if kind in (b"+", b"-", b":", b"_", b"#", b",", b"("):
            return kind + await reader.readuntil(b"\r\n")
        if kind in (b"$", b"!", b"="):
            line = await reader.readuntil(b"\r\n")
            length = int(line[:-2])
            if length < -1 or length > 8 * 1024 * 1024:
                raise ValueError("Unexpected Redis bulk length")
            return kind + line + (await reader.readexactly(length + 2) if length >= 0 else b"")
        if kind in (b"*", b"~", b">", b"%", b"|"):
            line = await reader.readuntil(b"\r\n")
            count = int(line[:-2])
            if count < -1 or count > 4096:
                raise ValueError("Unexpected Redis array length")
            items = b""
            for _ in range(max(0, count) * (2 if kind in (b"%", b"|") else 1)):
                items += await RespAckCutProxy._reply(reader)
            return kind + line + items
        raise ValueError(f"Unexpected RESP type {kind!r}, refusing fault attribution")

    async def _serve(self, client_reader, client_writer):
        upstream_reader, upstream_writer = await asyncio.open_connection(
            self.upstream_host, self.upstream_port
        )
        self._connections.update((client_writer, upstream_writer))

        async def upstream_to_client():
            while True:
                reply = await self._reply(upstream_reader)
                # NOSCRIPT must pass through so redis-py can fall back to EVAL.
                if self.armed and reply[:1] != b"-":
                    self.upstream_success_frame = True
                    self.captured.set()
                    await asyncio.wait_for(self.release_drop.wait(), timeout=12)
                    return
                client_writer.write(reply)
                await client_writer.drain()

        async def client_to_upstream():
            while True:
                chunk = await client_reader.read(65536)
                if not chunk:
                    return
                upstream_writer.write(chunk)
                await upstream_writer.drain()

        tasks = [asyncio.create_task(upstream_to_client()),
                 asyncio.create_task(client_to_upstream())]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            client_writer.close()
            upstream_writer.close()
            await asyncio.gather(
                client_writer.wait_closed(), upstream_writer.wait_closed(),
                return_exceptions=True
            )
            self._connections.difference_update((client_writer, upstream_writer))

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_):
        self.release_drop.set()
        self.server.close()
        await self.server.wait_closed()
        for connection in tuple(self._connections):
            connection.close()

    def arm_once(self):
        if self.armed or self.captured.is_set():
            raise RuntimeError("ACK-cut injection must be one-shot")
        self.armed = True

    async def wait_captured(self):
        await asyncio.wait_for(self.captured.wait(), timeout=10)
        if not self.upstream_success_frame or self.client_ack_bytes:
            raise AssertionError("Redis backend ACK was not captured exclusively")
