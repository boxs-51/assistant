"""Disposable PostgreSQL 17 PGwire COMMIT-response cut proxy.

Capture a physical CommandComplete(COMMIT) from PostgreSQL, withhold it from
the caller, independently observe COMMIT on another direct DB connection,
then disconnect. Only unencrypted test-only loopback PGwire is supported.
"""
import asyncio
import struct


class PgwireCommitAckCut:
    def __init__(self, upstream_host="127.0.0.1", upstream_port=5432):
        if upstream_host not in ("127.0.0.1", "localhost"):
            raise ValueError("Only disposable loopback PostgreSQL is allowed")
        self.upstream_host = upstream_host
        self.upstream_port = int(upstream_port)
        self.server = None
        self.port = None
        self.armed = False
        self.captured = asyncio.Event()
        self.release_drop = asyncio.Event()
        self.backend_commit_complete = False
        self.client_commit_ack_bytes = 0
        self._connections = set()

    async def _serve(self, client_reader, client_writer):
        upstream_reader, upstream_writer = await asyncio.open_connection(
            self.upstream_host, self.upstream_port
        )
        self._connections.update((client_writer, upstream_writer))

        async def server_to_client():
            while True:
                # PostgreSQL backend protocol: 1-byte type, 4-byte length,
                # and length-4 bytes of payload. SSL is disabled by test.
                tag = await upstream_reader.readexactly(1)
                nbytes = await upstream_reader.readexactly(4)
                length = struct.unpack("!I", nbytes)[0]
                if length < 4 or length > 16 * 1024 * 1024:
                    raise ValueError("Unexpected PGwire frame size or SSL negotiation")
                payload = await upstream_reader.readexactly(length - 4)
                frame = tag + nbytes + payload
                if self.armed and tag == b"C" and payload.rstrip(b"\x00") == b"COMMIT":
                    self.backend_commit_complete = True
                    self.captured.set()
                    await asyncio.wait_for(self.release_drop.wait(), timeout=12)
                    return
                client_writer.write(frame)
                await client_writer.drain()

        async def client_to_server():
            # Preserve frontend startup and extended-query messages as-is.
            while True:
                frame = await client_reader.read(65536)
                if not frame:
                    return
                upstream_writer.write(frame)
                await upstream_writer.drain()

        tasks = [asyncio.create_task(server_to_client()),
                 asyncio.create_task(client_to_server())]
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
            raise RuntimeError("PG COMMIT ACK-cut injection must be one-shot")
        self.armed = True

    async def wait_captured(self):
        await asyncio.wait_for(self.captured.wait(), timeout=10)
        if not self.backend_commit_complete or self.client_commit_ack_bytes:
            raise AssertionError("Physical PostgreSQL COMMIT confirmation not captured")
