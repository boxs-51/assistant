"""Deny-only AIC 31b external admission intent (issue #381).

This module deliberately has NO physical network mutator, migration runner,
DRAINED transition, release method, or production admission grant.  Its
etcd-backed records are intent, NOT proof of quiescence.  Operators MUST
retain independent network/DB endpoint exclusion and O1-O9 signoff.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import ssl
from typing import Protocol
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TICKET = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")
_STATES = {"SEAL_REQUESTED", "QUARANTINED", "OPEN"}


class BarrierDenied(RuntimeError):
    """Stale epoch, wrong actor, unsafe state, or inconsistent record."""


class BarrierUnavailable(BarrierDenied):
    """External authority was unavailable: always fail closed."""


@dataclass(frozen=True)
class Record:
    target_sha256: str
    epoch: int
    state: str
    ticket: str
    operator_sha256: str
    schema: int = 1

    def validate(self) -> None:
        if self.schema != 1 or not _HEX64.fullmatch(self.target_sha256):
            raise BarrierDenied("unrecognized target/schema")
        if not isinstance(self.epoch, int) or isinstance(self.epoch, bool) or self.epoch < 1:
            raise BarrierDenied("invalid epoch")
        if self.state not in _STATES or not _TICKET.fullmatch(self.ticket):
            raise BarrierDenied("invalid state/ticket")
        if not _HEX64.fullmatch(self.operator_sha256):
            raise BarrierDenied("invalid operator identity")

    @classmethod
    def decode(cls, data: bytes) -> "Record":
        try:
            obj = json.loads(data.decode("utf-8"))
            if not isinstance(obj, dict) or set(obj) != set(cls.__dataclass_fields__):
                raise ValueError("unexpected record fields")
            value = cls(**obj)
            value.validate()
            return value
        except (UnicodeError, ValueError, TypeError) as exc:
            raise BarrierDenied("invalid external barrier record") from exc

    def encode(self) -> bytes:
        self.validate()
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()


class Store(Protocol):
    def read(self, target: str) -> tuple[Record | None, int]: ...
    def cas(self, target: str, revision: int, value: Record) -> bool: ...


class EtcdV3Store:
    """etcd v3 TLS, linearizable range + server-side compare-and-swap.

    The external etcd cluster MUST be separately operated and hardened with
    mTLS/RBAC, quorum persistence and snapshot-rollback prevention.  A caller
    cannot certify these deployment properties with this module alone.
    """

    def __init__(self, *, endpoint: str, ca: str, cert: str, key: str,
                 timeout_seconds: float = 3.0):
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or not ca or not cert or not key
                or timeout_seconds <= 0 or timeout_seconds > 10):
            raise BarrierDenied("explicit trusted HTTPS etcd endpoint and mTLS required")
        ctx = ssl.create_default_context(cafile=ca)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        self._endpoint = endpoint.rstrip("/")
        self._timeout = timeout_seconds
        self._opener = build_opener(HTTPSHandler(context=ctx), _NoRedirect())

    @staticmethod
    def _key(target: str) -> str:
        if not _HEX64.fullmatch(target):
            raise BarrierDenied("target SHA256 required")
        return base64.b64encode(("/aic31b/barrier/v1/" + target).encode()).decode()

    def _post(self, method: str, payload: dict) -> dict:
        try:
            req = Request(
                self._endpoint + method,
                data=json.dumps(payload, separators=(",", ":")).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self._opener.open(req, timeout=self._timeout) as response:
                value = json.loads(response.read(1024 * 1024))
            if not isinstance(value, dict):
                raise ValueError("non-object response")
            return value
        except Exception as exc:
            raise BarrierUnavailable("external etcd read/CAS not verified") from exc

    def read(self, target: str) -> tuple[Record | None, int]:
        payload = self._post("/v3/kv/range", {"key": self._key(target), "serializable": False})
        try:
            count = int(payload["count"])
            if count == 0 and not payload.get("kvs"):
                return None, 0
            if count != 1 or len(payload["kvs"]) != 1:
                raise ValueError("not one record")
            kv = payload["kvs"][0]
            if kv["key"] != self._key(target):
                raise ValueError("unexpected key")
            revision = int(kv["mod_revision"])
            if revision < 1:
                raise ValueError("invalid revision")
            record = Record.decode(base64.b64decode(kv["value"], validate=True))
            if record.target_sha256 != target:
                raise ValueError("target mismatch")
            return record, revision
        except (KeyError, ValueError, TypeError, BarrierDenied) as exc:
            raise BarrierUnavailable("external record missing or untrusted") from exc

    def cas(self, target: str, revision: int, value: Record) -> bool:
        if value.target_sha256 != target or revision < 0:
            raise BarrierDenied("CAS target/revision mismatch")
        compare = {"key": self._key(target)}
        if revision == 0:
            compare.update({"target": "VERSION", "version": "0"})
        else:
            compare.update({"target": "MOD", "mod_revision": str(revision)})
        payload = {
            "compare": [compare],
            "success": [{"request_put": {
                "key": self._key(target),
                "value": base64.b64encode(value.encode()).decode(),
            }}],
            "failure": [],
        }
        response = self._post("/v3/kv/txn", payload)
        if type(response.get("succeeded")) is not bool:
            raise BarrierUnavailable("untrusted CAS response")
        return response["succeeded"]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BarrierUnavailable("etcd redirect forbidden")


class DenyOnlyBarrier:
    """A CAS-backed epoch receipt.  No API here can physically fence or OPEN."""

    def __init__(self, *, store: Store, target_sha256: str,
                 actor_cert_sha256: str, operator_cert_sha256: str,
                 reviewer_cert_sha256: str):
        for digest in (target_sha256, actor_cert_sha256,
                       operator_cert_sha256, reviewer_cert_sha256):
            if not _HEX64.fullmatch(digest):
                raise BarrierDenied("all identities must be pinned SHA256")
        if operator_cert_sha256 == reviewer_cert_sha256:
            raise BarrierDenied("operator and reviewer must be distinct certificates")
        self.store = store
        self.target = target_sha256
        self.actor = actor_cert_sha256
        self.operator = operator_cert_sha256
        self.reviewer = reviewer_cert_sha256

    def inspect(self) -> Record | None:
        record, revision = self.store.read(self.target)
        if record is None and revision != 0:
            raise BarrierDenied("missing record with nonzero revision")
        if record is not None and record.target_sha256 != self.target:
            raise BarrierDenied("wrong target")
        return record

    def seal(self, *, expected_epoch: int, ticket: str) -> Record:
        if self.actor != self.operator:
            raise BarrierDenied("seal requires pinned operator certificate")
        if (not isinstance(expected_epoch, int) or isinstance(expected_epoch, bool)
                or expected_epoch < 0 or not _TICKET.fullmatch(ticket)):
            raise BarrierDenied("explicit epoch and ticket required")
        prior, revision = self.store.read(self.target)
        current_epoch = prior.epoch if prior else 0
        if expected_epoch != current_epoch:
            raise BarrierDenied("stale expected epoch")
        if prior and prior.state != "OPEN":
            if (prior.state == "SEAL_REQUESTED" and prior.ticket == ticket
                    and prior.operator_sha256 == self.actor):
                return prior  # same intent, not a new authority
            raise BarrierDenied("unfinished/unknown epoch: quarantine and investigate")
        next_record = Record(target_sha256=self.target, epoch=current_epoch + 1,
                             state="SEAL_REQUESTED", ticket=ticket,
                             operator_sha256=self.actor)
        if not self.store.cas(self.target, revision, next_record):
            raise BarrierDenied("concurrent barrier CAS lost")
        return next_record

    def quarantine(self, *, expected_epoch: int) -> Record:
        if self.actor not in (self.operator, self.reviewer):
            raise BarrierDenied("quarantine requires pinned actor")
        prior, revision = self.store.read(self.target)
        if prior is None or prior.epoch != expected_epoch:
            raise BarrierDenied("stale/missing epoch")
        if prior.state == "QUARANTINED":
            return prior
        record = Record(target_sha256=self.target, epoch=prior.epoch,
                        state="QUARANTINED", ticket=prior.ticket,
                        operator_sha256=prior.operator_sha256)
        if not self.store.cas(self.target, revision, record):
            raise BarrierDenied("concurrent quarantine CAS lost")
        return record

    def require_admitted(self) -> None:
        # Never grant admission based on S0: even a record called OPEN could
        # be stale/restored, or physical D1 deny might not exist.
        raise BarrierDenied("S0 has no independently verified physical release")


def certificate_sha256(pem_path: str) -> str:
    """Public TLS client certificate fingerprint; private key is never logged."""
    try:
        content = Path(pem_path).read_text(encoding="ascii")
        der = ssl.PEM_cert_to_DER_cert(content)
        return hashlib.sha256(der).hexdigest()
    except (OSError, ValueError) as exc:
        raise BarrierDenied("cannot verify client certificate") from exc
