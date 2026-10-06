#!/usr/bin/env python3
"""AE-R13-C rollout evidence collector.

Reads production structlog JSONL, projects only the frozen R13-C0 telemetry
fields, queries the durable AgentExecution population read-only, and writes a
bounded R13-C evidence package. It never mutates the database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

EXPECTED_C0_SHA = "5a75ac1483e15fecb91fbdeaa8626e5b62c4c54e"

EVENT_INVENTORY = "ae_r13_legacy_continuation_inventory"
EVENT_BATCH = "ae_r13_legacy_continuation_materialization_batch"
EVENT_FAILED = "ae_r13_legacy_continuation_materialization_failed"
EVENTS = {EVENT_INVENTORY, EVENT_BATCH, EVENT_FAILED}

INVENTORY_FIELDS = (
    "candidate_count",
    "continuation_candidate_count",
    "missing_continuation_count",
)
BATCH_FIELDS = (
    "attempted_count",
    "materialized_count",
    "rejected_count",
)


def parse_ts(value: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"timestamp must include timezone: {value!r}")
    return dt.astimezone(timezone.utc)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def first_json_object(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    candidates = [line]
    idx = line.find("{")
    if idx > 0:
        candidates.append(line[idx:])
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def coerce_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer, got bool")
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer, got {value!r}") from exc
    if out < 0:
        raise ValueError(f"{field} must be non-negative, got {out}")
    return out


def project_event(obj: dict[str, Any]) -> dict[str, Any] | None:
    event = obj.get("event")
    if event not in EVENTS:
        return None
    ts_raw = obj.get("timestamp")
    if not isinstance(ts_raw, str):
        raise ValueError(f"{event}: missing string timestamp")
    projected: dict[str, Any] = {
        "timestamp": iso_utc(parse_ts(ts_raw)),
        "event": event,
        "level": str(obj.get("level", "")).lower() or None,
    }
    if event == EVENT_INVENTORY:
        for field in INVENTORY_FIELDS:
            projected[field] = coerce_nonnegative_int(obj.get(field), field)
    elif event == EVENT_BATCH:
        for field in BATCH_FIELDS:
            projected[field] = coerce_nonnegative_int(obj.get(field), field)
    else:
        error_type = obj.get("error_type")
        if not isinstance(error_type, str) or not error_type.strip():
            raise ValueError(f"{event}: missing bounded error_type")
        projected["error_type"] = error_type.strip()
    return projected


def load_events(
    paths: Iterable[Path],
    start: datetime,
    end: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in paths:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                obj = first_json_object(line)
                if obj is None or obj.get("event") not in EVENTS:
                    continue
                try:
                    ev = project_event(obj)
                except Exception as exc:
                    errors.append(f"{path}:{lineno}: {exc}")
                    continue
                assert ev is not None
                ts = parse_ts(ev["timestamp"])
                if start <= ts <= end:
                    events.append(ev)
    events.sort(key=lambda item: item["timestamp"])
    return events, errors


@dataclass(frozen=True)
class Window:
    name: str
    start: datetime
    end: datetime


def summarize_cycle(
    events: list[dict[str, Any]],
    window: Window,
) -> dict[str, Any]:
    selected = [
        event
        for event in events
        if window.start <= parse_ts(event["timestamp"]) <= window.end
    ]
    inventories = [e for e in selected if e["event"] == EVENT_INVENTORY]
    batches = [e for e in selected if e["event"] == EVENT_BATCH]
    failed = [e for e in selected if e["event"] == EVENT_FAILED]
    return {
        "start_utc": iso_utc(window.start),
        "end_utc": iso_utc(window.end),
        "inventory_event_count": len(inventories),
        "batch_event_count": len(batches),
        "failed_event_count": len(failed),
        "candidate_count": sum(e["candidate_count"] for e in inventories),
        "continuation_candidate_count": sum(
            e["continuation_candidate_count"] for e in inventories
        ),
        "missing_continuation_count": sum(
            e["missing_continuation_count"] for e in inventories
        ),
        "attempted_count": sum(e["attempted_count"] for e in batches),
        "materialized_count": sum(e["materialized_count"] for e in batches),
        "rejected_count": sum(e["rejected_count"] for e in batches),
        "error_types": sorted({e["error_type"] for e in failed}),
        "sanitized_events": selected,
    }


def sqlite_inventory(path: Path) -> dict[str, int]:
    db_path = path.resolve()
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS active_legacy_waiting_without_checkpoint,
                COALESCE(SUM(CASE
                    WHEN json_type(context_state, '$.continuation') = 'object'
                    THEN 1 ELSE 0 END), 0) AS continuation_mapping_present,
                COALESCE(SUM(CASE
                    WHEN json_type(context_state, '$.continuation') = 'object'
                    THEN 0 ELSE 1 END), 0)
                    AS continuation_missing_or_non_mapping
            FROM agent_executions
            WHERE state IN ('WAITING', 'WAITING_FOR_CONNECTION')
              AND (wait_reason = 'CONNECTION' OR wait_reason IS NULL)
              AND current_checkpoint_id IS NULL
            """
        ).fetchone()
        assert row is not None
        return {
            "active_legacy_waiting_without_checkpoint": int(row[0]),
            "continuation_mapping_present": int(row[1]),
            "continuation_missing_or_non_mapping": int(row[2]),
        }
    finally:
        conn.close()


def normalize_pg_async_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    raise ValueError(
        "PostgreSQL URL must start with postgres://, postgresql://, "
        "or postgresql+asyncpg://"
    )


async def postgres_inventory(url: str) -> dict[str, int]:
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL mode requires SQLAlchemy and asyncpg from the repo environment"
        ) from exc

    engine = create_async_engine(normalize_pg_async_url(url), pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            try:
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                result = await conn.execute(
                    text(
                        """
                        SELECT
                            COUNT(*) AS active_legacy_waiting_without_checkpoint,
                            COALESCE(SUM(CASE
                                WHEN jsonb_typeof(
                                    context_state::jsonb -> 'continuation'
                                ) = 'object'
                                THEN 1 ELSE 0 END), 0)
                                AS continuation_mapping_present,
                            COALESCE(SUM(CASE
                                WHEN jsonb_typeof(
                                    context_state::jsonb -> 'continuation'
                                ) = 'object'
                                THEN 0 ELSE 1 END), 0)
                                AS continuation_missing_or_non_mapping
                        FROM agent_executions
                        WHERE state IN ('WAITING', 'WAITING_FOR_CONNECTION')
                          AND (wait_reason = 'CONNECTION' OR wait_reason IS NULL)
                          AND current_checkpoint_id IS NULL
                        """
                    )
                )
                row = result.mappings().one()
                return {
                    "active_legacy_waiting_without_checkpoint": int(
                        row["active_legacy_waiting_without_checkpoint"]
                    ),
                    "continuation_mapping_present": int(
                        row["continuation_mapping_present"]
                    ),
                    "continuation_missing_or_non_mapping": int(
                        row["continuation_missing_or_non_mapping"]
                    ),
                }
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()


def classify(
    cycle_a: dict[str, Any],
    cycle_b: dict[str, Any],
    inventory: dict[str, int],
    args: argparse.Namespace,
    parser_errors: list[str],
    events: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    reasons: list[str] = []

    if parser_errors:
        reasons.append("telemetry parser encountered malformed R13-C event lines")
    if args.deployed_sha != args.expected_sha:
        reasons.append(
            "deployed SHA differs from independently approved expected SHA; "
            "fresh drift audit required"
        )
    if args.server_restart_count < 1:
        reasons.append("no server restart/deploy cycle recorded")
    if args.log_sampling != "NONE":
        reasons.append("log sampling/completeness is not certified NONE")

    if any(e["event"] == EVENT_FAILED for e in events):
        reasons.append("observation window contains materialization_failed event(s)")
    if sum(e.get("missing_continuation_count", 0) for e in events) > 0:
        reasons.append("observation window missing_continuation_count > 0")
    if sum(e.get("rejected_count", 0) for e in events) > 0:
        reasons.append("observation window rejected_count > 0")

    for event in events:
        if event["event"] == EVENT_INVENTORY and (
            event["candidate_count"]
            != event["continuation_candidate_count"]
            + event["missing_continuation_count"]
        ):
            reasons.append(
                "observation window contains internally inconsistent inventory event"
            )
            break
    for event in events:
        if event["event"] == EVENT_BATCH and (
            event["attempted_count"]
            != event["materialized_count"] + event["rejected_count"]
        ):
            reasons.append("observation window contains unexplained batch outcome gap")
            break

    for name, cycle in (("A", cycle_a), ("B", cycle_b)):
        if cycle["inventory_event_count"] < 1:
            reasons.append(
                f"Cycle {name} did not prove pending-ticket path exercise "
                "(no inventory event)"
            )
        if cycle["batch_event_count"] < 1:
            reasons.append(f"Cycle {name} lacks normal batch-completion evidence")
        if cycle["failed_event_count"] > 0:
            reasons.append(f"Cycle {name} contains materialization_failed event(s)")
        if cycle["missing_continuation_count"] > 0:
            reasons.append(f"Cycle {name} missing_continuation_count > 0")
        if cycle["rejected_count"] > 0:
            reasons.append(f"Cycle {name} rejected_count > 0")
        if (
            cycle["attempted_count"]
            != cycle["materialized_count"] + cycle["rejected_count"]
        ):
            reasons.append(
                f"Cycle {name} has unexplained attempted/materialized/rejected gap"
            )
        if cycle["attempted_count"] != cycle["candidate_count"]:
            reasons.append(
                f"Cycle {name} has unexplained selected-candidate/attempted gap"
            )
        if (
            cycle["candidate_count"]
            != cycle["continuation_candidate_count"]
            + cycle["missing_continuation_count"]
        ):
            reasons.append(
                f"Cycle {name} inventory counters are internally inconsistent"
            )

    db_zero = all(value == 0 for value in inventory.values())
    if not db_zero:
        reasons.append("post-window durable active-legacy inventory is non-zero")

    count_keys = (
        "candidate_count",
        "continuation_candidate_count",
        "missing_continuation_count",
        "attempted_count",
        "materialized_count",
        "rejected_count",
    )
    zero_a = all(cycle_a[key] == 0 for key in count_keys)
    zero_b = all(cycle_b[key] == 0 for key in count_keys)

    mode_z = zero_a and zero_b and db_zero
    mode_c = (
        cycle_a["candidate_count"] > 0
        and cycle_a["continuation_candidate_count"] == cycle_a["candidate_count"]
        and cycle_a["missing_continuation_count"] == 0
        and cycle_a["attempted_count"] > 0
        and cycle_a["materialized_count"] == cycle_a["attempted_count"]
        and cycle_a["rejected_count"] == 0
        and zero_b
        and db_zero
    )

    if reasons:
        return "HOLD", reasons
    if mode_z:
        return "PASS-MODE-Z", []
    if mode_c:
        return "PASS-MODE-C", []
    return "HOLD", [
        "Cycle A/B counters do not match PASS-MODE-Z or PASS-MODE-C"
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect bounded AE-R13-C rollout evidence"
    )
    parser.add_argument(
        "--log",
        action="append",
        required=True,
        type=Path,
        help="JSONL log file; repeat for multiple files",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--deployed-sha", required=True)
    parser.add_argument("--expected-sha", default=EXPECTED_C0_SHA)
    parser.add_argument("--environment-class", required=True)
    parser.add_argument("--server-restart-count", required=True, type=int)
    parser.add_argument(
        "--log-sampling",
        choices=("NONE", "EXPLAIN"),
        required=True,
    )
    parser.add_argument("--sampling-note", default="")
    parser.add_argument("--window-start", required=True)
    parser.add_argument("--window-end", required=True)
    parser.add_argument("--cycle-a-start", required=True)
    parser.add_argument("--cycle-a-end", required=True)
    parser.add_argument("--cycle-b-start", required=True)
    parser.add_argument("--cycle-b-end", required=True)
    db = parser.add_mutually_exclusive_group(required=True)
    db.add_argument("--sqlite-path", type=Path)
    db.add_argument("--postgres-url")
    return parser


async def async_main(args: argparse.Namespace) -> int:
    window_start = parse_ts(args.window_start)
    window_end = parse_ts(args.window_end)
    cycle_a_window = Window(
        "A",
        parse_ts(args.cycle_a_start),
        parse_ts(args.cycle_a_end),
    )
    cycle_b_window = Window(
        "B",
        parse_ts(args.cycle_b_start),
        parse_ts(args.cycle_b_end),
    )

    if not (
        window_start
        <= cycle_a_window.start
        <= cycle_a_window.end
        <= window_end
    ):
        raise ValueError("Cycle A must be fully inside the observation window")
    if not (
        window_start
        <= cycle_b_window.start
        <= cycle_b_window.end
        <= window_end
    ):
        raise ValueError("Cycle B must be fully inside the observation window")
    if cycle_a_window.end > cycle_b_window.start:
        raise ValueError("Cycle A must finish before Cycle B starts")

    for path in args.log:
        if not path.is_file():
            raise FileNotFoundError(path)

    events, parser_errors = load_events(
        args.log,
        window_start,
        window_end,
    )
    cycle_a = summarize_cycle(events, cycle_a_window)
    cycle_b = summarize_cycle(events, cycle_b_window)

    if args.sqlite_path is not None:
        inventory = sqlite_inventory(args.sqlite_path)
        db_mode = "sqlite-read-only-uri"
    else:
        inventory = await postgres_inventory(args.postgres_url)
        db_mode = "postgresql-transaction-read-only"

    result, hold_reasons = classify(
        cycle_a,
        cycle_b,
        inventory,
        args,
        parser_errors,
        events,
    )

    totals = {
        "INVENTORY_EVENT_COUNT": sum(
            1 for e in events if e["event"] == EVENT_INVENTORY
        ),
        "BATCH_EVENT_COUNT": sum(
            1 for e in events if e["event"] == EVENT_BATCH
        ),
        "FAILED_EVENT_COUNT": sum(
            1 for e in events if e["event"] == EVENT_FAILED
        ),
        "SUM_CANDIDATE_COUNT": sum(
            e.get("candidate_count", 0) for e in events
        ),
        "SUM_CONTINUATION_CANDIDATE_COUNT": sum(
            e.get("continuation_candidate_count", 0) for e in events
        ),
        "SUM_MISSING_CONTINUATION_COUNT": sum(
            e.get("missing_continuation_count", 0) for e in events
        ),
        "SUM_ATTEMPTED_COUNT": sum(
            e.get("attempted_count", 0) for e in events
        ),
        "SUM_MATERIALIZED_COUNT": sum(
            e.get("materialized_count", 0) for e in events
        ),
        "SUM_REJECTED_COUNT": sum(
            e.get("rejected_count", 0) for e in events
        ),
    }

    package: dict[str, Any] = {
        "SCHEMA": "AE-R13-C-ROLLOUT-EVIDENCE-V1",
        "DEPLOYED_SHA": args.deployed_sha,
        "EXPECTED_SHA": args.expected_sha,
        "WINDOW_START_UTC": iso_utc(window_start),
        "WINDOW_END_UTC": iso_utc(window_end),
        "ENVIRONMENT_CLASS": args.environment_class,
        "SERVER_RESTART_COUNT": args.server_restart_count,
        "CONTROLLED_RECONNECT_CYCLES": 2,
        **totals,
        "POST_WINDOW_ACTIVE_LEGACY_WAITING_WITHOUT_CHECKPOINT": inventory[
            "active_legacy_waiting_without_checkpoint"
        ],
        "POST_WINDOW_CONTINUATION_MAPPING_PRESENT": inventory[
            "continuation_mapping_present"
        ],
        "POST_WINDOW_CONTINUATION_MISSING_OR_NON_MAPPING": inventory[
            "continuation_missing_or_non_mapping"
        ],
        "LOG_SAMPLING": args.log_sampling,
        "LOG_SAMPLING_NOTE": args.sampling_note,
        "RETENTION": "KEEP_READ_ONLY",
        "DATABASE_READ_ONLY_MODE": db_mode,
        "RESULT": result,
        "HOLD_REASONS": hold_reasons,
        "PARSER_ERRORS": parser_errors,
        "CYCLES": {"A": cycle_a, "B": cycle_b},
        "SANITIZED_WINDOW_EVENTS": events,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(package, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "output": str(args.output),
                "result": result,
                "hold_reasons": hold_reasons,
                "durable_inventory": inventory,
                "cycle_a": {
                    key: value
                    for key, value in cycle_a.items()
                    if key != "sanitized_events"
                },
                "cycle_b": {
                    key: value
                    for key, value in cycle_b.items()
                    if key != "sanitized_events"
                },
            },
            indent=2,
        )
    )
    return 0 if result.startswith("PASS-") else 2


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(async_main(args))
    except Exception as exc:
        print(
            f"R13-C evidence collection failed closed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
