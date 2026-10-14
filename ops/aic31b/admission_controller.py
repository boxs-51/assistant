"""Operator-side deny-only AIC31b epoch CLI. NEVER invokes a migration.

All commands require a separate mTLS-protected, RBAC-scoped etcd authority.
Use only after checking target fingerprint and controller custody; inspect
does NOT prove D1 physical network exclusion or any O1-O9 gate.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from typing import Mapping

from ops.aic31b.barrier import (
    BarrierDenied, DenyOnlyBarrier, EtcdV3Store, certificate_sha256,
)


_REQUIRED = (
    "AIC31B_ETCD_URL", "AIC31B_ETCD_CA", "AIC31B_ETCD_CERT",
    "AIC31B_ETCD_KEY", "AIC31B_TARGET_SHA256",
    "AIC31B_OPERATOR_CERT_SHA256", "AIC31B_REVIEWER_CERT_SHA256",
)


def configured_barrier(env: Mapping[str, str]) -> DenyOnlyBarrier:
    missing = [key for key in _REQUIRED if not env.get(key)]
    if missing:
        raise BarrierDenied("missing explicit barrier configuration: " + ",".join(missing))
    return DenyOnlyBarrier(
        store=EtcdV3Store(
            endpoint=env["AIC31B_ETCD_URL"],
            ca=env["AIC31B_ETCD_CA"],
            cert=env["AIC31B_ETCD_CERT"],
            key=env["AIC31B_ETCD_KEY"],
        ),
        target_sha256=env["AIC31B_TARGET_SHA256"],
        actor_cert_sha256=certificate_sha256(env["AIC31B_ETCD_CERT"]),
        operator_cert_sha256=env["AIC31B_OPERATOR_CERT_SHA256"],
        reviewer_cert_sha256=env["AIC31B_REVIEWER_CERT_SHA256"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AIC31b external S0 epoch INTENT only (not a DB admission grant)"
    )
    group = parser.add_subparsers(dest="command", required=True)
    group.add_parser("inspect")
    seal = group.add_parser("seal")
    seal.add_argument("--expected-epoch", type=int, required=True)
    seal.add_argument("--ticket", required=True)
    quarantine = group.add_parser("quarantine")
    quarantine.add_argument("--expected-epoch", type=int, required=True)
    args = parser.parse_args(argv)

    try:
        control = configured_barrier(os.environ)
        if args.command == "inspect":
            record = control.inspect()
        elif args.command == "seal":
            record = control.seal(
                expected_epoch=args.expected_epoch, ticket=args.ticket
            )
        else:
            record = control.quarantine(expected_epoch=args.expected_epoch)
        print(json.dumps(
            {"admission": "DENY", "physical_fence_verified": False,
             "record": asdict(record) if record is not None else None},
            sort_keys=True,
        ))
        return 0
    except BarrierDenied as exc:
        # Do not print DSNs, cert paths or network error response bodies.
        print(json.dumps({"admission": "DENY", "error": str(exc)}, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
