"""SBX3 S1-A private, non-activated, authenticated-transport protocol.

S1-A never delegates execution authority. An OS-authenticated PING may return a
transport observation; all model/native effects are unconditionally DENIED.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import struct
import sys
import time
from typing import Any

VERSION = 1
MAX_FRAME = 4096
ACTIONS = frozenset({"PING", "MINT", "SPAWN", "GET_POSITIVE_RECEIPT", "DELETE_ROOT", "VERIFIED_EMPTY"})
EFFECTS = ACTIONS - {"PING"}
_NONCE = re.compile(r"[a-f0-9]{32}\Z", flags=re.ASCII)
_GEN = re.compile(r"[1-9][0-9]*\Z", flags=re.ASCII)


class S1AProtocolError(ValueError):
    pass


def canonical_generation(value: Any) -> str:
    """Reject aliases, bool, sign, whitespace, zero and 64-bit overflow."""
    if isinstance(value, bool):
        raise S1AProtocolError("generation boolean")
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not _GEN.fullmatch(value):
        raise S1AProtocolError("noncanonical generation")
    if len(value) > 19 or int(value) > 9223372036854775807:
        raise S1AProtocolError("generation overflow")
    return value


def encode_frame(value: dict[str, Any]) -> bytes:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_FRAME:
        raise S1AProtocolError("oversized frame")
    return struct.pack("!I", len(raw)) + raw


def decode_frame(frame: bytes) -> dict[str, Any]:
    if len(frame) < 4:
        raise S1AProtocolError("truncated header")
    size = struct.unpack("!I", frame[:4])[0]
    if size == 0 or size > MAX_FRAME or len(frame) != size + 4:
        raise S1AProtocolError("invalid frame length")
    def pairs_unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise S1AProtocolError("duplicate JSON key")
            result[key] = value
        return result
    try:
        obj = json.loads(frame[4:].decode("utf-8"), object_pairs_hook=pairs_unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(S1AProtocolError("nonfinite")))
    except (ValueError, UnicodeError) as exc:
        raise S1AProtocolError("invalid JSON frame") from exc
    if not isinstance(obj, dict):
        raise S1AProtocolError("message must be an object")
    return obj


def new_request(action: str = "PING", *, generation: Any = "1",
                broker_epoch: str = "", revision: int = 0,
                nonce: str | None = None, deadline_unix: float | None = None) -> dict[str, Any]:
    if action not in ACTIONS:
        raise S1AProtocolError("unsupported action")
    return {"version": VERSION, "action": action, "generation": canonical_generation(generation),
            "nonce": nonce or secrets.token_hex(16), "deadline_unix": deadline_unix or (time.time() + 20),
            "broker_epoch": broker_epoch, "revision": revision}


class DenyOnlyProtocol:
    """In-memory replay fence; never an OS authority or successful receipt."""

    def __init__(self, broker_epoch: str) -> None:
        if not _NONCE.fullmatch(broker_epoch):
            raise S1AProtocolError("invalid broker epoch")
        self.epoch = broker_epoch
        self._seen: set[str] = set()

    def respond(self, request: dict[str, Any], *, peer_authenticated: bool) -> dict[str, Any]:
        response: dict[str, Any] = {"version": VERSION, "status": "DENY",
                                    "broker_epoch": self.epoch, "peer_authenticated": False}
        try:
            if set(request) != {"version", "action", "generation", "nonce", "deadline_unix", "broker_epoch", "revision"}:
                raise S1AProtocolError("schema mismatch")
            if type(request["version"]) is not int or request["version"] != VERSION:
                raise S1AProtocolError("version mismatch")
            if request["action"] not in ACTIONS:
                raise S1AProtocolError("action mismatch")
            canonical_generation(request["generation"])
            nonce = request["nonce"]
            if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
                raise S1AProtocolError("invalid nonce")
            if nonce in self._seen:
                raise S1AProtocolError("replay")
            if (type(request["deadline_unix"]) not in (int, float)
                    or not time.time() < request["deadline_unix"] <= time.time() + 30):
                raise S1AProtocolError("stale deadline")
            if type(request["revision"]) is not int or request["revision"] != 0:
                raise S1AProtocolError("revision unsupported")
            if request["broker_epoch"] != self.epoch:
                raise S1AProtocolError("epoch mismatch")
            if not peer_authenticated:
                raise S1AProtocolError("foreign OS principal")
            self._seen.add(nonce)
            response["peer_authenticated"] = True
            response["reason"] = ("TRANSPORT_ONLY_NO_EFFECT_AUTHORITY" if request["action"] == "PING"
                                  else "S1A_NATIVE_EFFECT_NOT_RELEASED")
        except (S1AProtocolError, TypeError, KeyError, ValueError) as exc:
            response["reason"] = str(exc)[:96]
        return response


def _cli() -> int:
    parser = argparse.ArgumentParser(description="S1-A private IPC transport probe; NEVER authorizes native effects")
    parser.add_argument("--linux-socket")
    parser.add_argument("--windows-pipe")
    parser.add_argument("--epoch", required=True)
    parser.add_argument("--action", default="PING")
    parser.add_argument("--generation", default="1")
    args = parser.parse_args()
    request = new_request(args.action, generation=args.generation, broker_epoch=args.epoch)
    if bool(args.linux_socket) == bool(args.windows_pipe):
        parser.error("choose exactly one private transport")
    if args.linux_socket:
        from .native_broker_ipc_linux_s1a import client_request
        result = client_request(args.linux_socket, request)
    else:
        from .native_broker_ipc_windows_s1a import client_request
        result = client_request(args.windows_pipe, request)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "DENY" else 2


if __name__ == "__main__":
    sys.exit(_cli())
