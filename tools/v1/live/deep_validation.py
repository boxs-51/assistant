"""Opt-in, bounded real-machine probes for Tools V1.

Run from the repository root with RUN_TOOLS_V1_LIVE=1. The only writable
targets are a fresh system temporary directory and the output JSON path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.v1 import desktop_tool, file_tool, find_by_glob, terminal_tool, window_tool
from tools.v1 import web_tool
from tools.v1.live.harness import validate_tool_result


def _bounded(value, *, max_string=240, max_items=3):
    if isinstance(value, str):
        return value if len(value) <= max_string else {"length": len(value), "prefix": value[:max_string]}
    if isinstance(value, list):
        return {"length": len(value), "sample": [_bounded(x) for x in value[:max_items]]} if len(value) > max_items else [_bounded(x) for x in value]
    if isinstance(value, dict):
        return {str(k): _bounded(v) for k, v in value.items()}
    return value


def main() -> int:
    if os.environ.get("RUN_TOOLS_V1_LIVE") != "1":
        print("RUN_TOOLS_V1_LIVE=1 required", file=sys.stderr)
        return 2
    if len(sys.argv) != 2:
        print("usage: python -m tools.v1.live.deep_validation OUTPUT_JSON", file=sys.stderr)
        return 2
    output = Path(sys.argv[1]).resolve()
    if output.exists():
        print("output already exists", file=sys.stderr)
        return 2

    records = []
    with tempfile.TemporaryDirectory(prefix="tools-v1-deep-") as tmp:
        root = Path(tmp)

        def record(name, result, expected_ok=None, expected_code=None, check=None):
            validated = validate_tool_result(result)
            error = validated["error"]
            code = error.get("code") if isinstance(error, dict) else None
            passed = (expected_ok is None or validated["ok"] is expected_ok) and (expected_code is None or code == expected_code)
            if check is not None:
                passed = passed and bool(check(validated))
            records.append({
                "name": name,
                "pass": passed,
                "response": _bounded(validated),
            })
            print(f"{'PASS' if passed else 'FAIL'} {name}: ok={validated['ok']} code={code}", flush=True)
            return validated

        small = root / "small.txt"
        record("file_write", file_tool.run("write", str(small), content="alpha\nbeta\nalpha\n"), True)
        record("file_read_page", file_tool.run("read", str(small), start_line=2, num_lines=1), True)
        record("file_search_regex", file_tool.run("search", str(small), queries=r"a[a-z]+a", use_regex=True), True)
        record("file_replace", file_tool.run("replace", str(small), queries="alpha", replacements="gamma"), True)
        record("file_read_after_replace", file_tool.run("read", str(small)), True, check=lambda r: r["data"]["content"] == "gamma\nbeta\ngamma\n")
        record("file_missing", file_tool.run("read", str(root / "missing.txt")), False)
        record("file_invalid_regex", file_tool.run("search", str(small), queries="[", use_regex=True), False)

        data = root / "data"
        data.mkdir()
        for i in range(600):
            (data / f"item-{i:04d}.txt").write_text("needle\n" * 10, encoding="utf-8")
        paths = [str(data / f"item-{i:04d}.txt") for i in range(80)]
        record("glob_600_limited", find_by_glob.run("*.txt", str(data), recursive=False, max_results=25), True, check=lambda r: r["data"]["returned_count"] == 25 and r["meta"]["truncated"] is True)
        record("glob_600_full", find_by_glob.run("*.txt", str(data), recursive=False, max_results=1000), True, check=lambda r: r["data"]["returned_count"] == 600 and r["meta"]["truncated"] is False)
        record("glob_invalid_limit", find_by_glob.run("*.txt", str(data), max_results=5001), False)
        record("file_search_80_files_limit", file_tool.run("search", paths, queries="needle", max_results_per_file=5), False, "INVALID_ARGUMENT")
        record("file_search_32_files_320_hits", file_tool.run("search", paths[:32], queries="needle", max_results_per_file=5), True, check=lambda r: r["data"]["total_match_count"] == 320 and r["data"]["returned_count"] == 160 and r["meta"]["truncated"] is True)
        large = data / "large.txt"
        record("file_write_1m", file_tool.run("write", str(large), content="x" * 900_000), True)
        record("file_read_long_line_limit", file_tool.run("read", str(large), max_chars=4096), False, "OUTPUT_LIMIT_EXCEEDED")
        multiline = data / "multiline.txt"
        record("file_write_900k_multiline", file_tool.run("write", str(multiline), content=("x" * 2999 + "\n") * 300), True)
        record("file_read_900k_cap", file_tool.run("read", str(multiline), max_chars=4096), True, check=lambda r: len(r["data"]["content"]) == 3000 and r["data"]["size_bytes"] == 900000 and r["meta"]["truncated"] is True)

        python = subprocess.list2cmdline([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)"])
        record("terminal_nonzero", terminal_tool.run("run", python, cwd=tmp, timeout=5), True, check=lambda r: r["data"]["exit_code"] == 7 and "out" in r["data"]["stdout"] and "err" in r["data"]["stderr"])
        python = subprocess.list2cmdline([sys.executable, "-c", "import time; time.sleep(3)"])
        record("terminal_timeout", terminal_tool.run("run", python, cwd=tmp, timeout=1), False, "TERMINAL_TIMEOUT")
        python = subprocess.list2cmdline([sys.executable, "-c", "import sys; sys.stdout.write('x'*5000000)"])
        record("terminal_output_limit", terminal_tool.run("run", python, cwd=tmp, timeout=10), False, "TERMINAL_OUTPUT_LIMIT")
        record("terminal_invalid_cwd", terminal_tool.run("run", "echo ok", cwd=str(root / "missing")), False)

        record("web_invalid_url", web_tool.run("scrape", url="file:///etc/passwd"), False)
        record("web_invalid_action", web_tool.run("not_an_action"), False)
        if os.environ.get("RUN_TOOLS_V1_LIVE_NETWORK") == "1":
            record("web_search_10", web_tool.run("search", query="IANA example domain", max_results=10, timeout=20), True, check=lambda r: r["data"]["returned_count"] == 10 and len(r["data"]["results"]) == 10)
            record("web_scrape_many_2", web_tool.run("scrape_many", urls=["https://example.com", "https://www.iana.org/domains/reserved"], timeout=20, max_chars=5000), True, check=lambda r: r["data"]["requested_count"] == 2 and r["data"]["succeeded_count"] == 2 and r["data"]["failed_count"] == 0)
        record("window_invalid_action", window_tool.run("not_an_action"), False)
        record("desktop_invalid_action", desktop_tool.run("not_an_action"), False)

        if os.environ.get("RUN_TOOLS_V1_LIVE_GUI") == "1":
            from tools.v1.live.gui_scenarios import run_gui_live

            def capture(name, run):
                def wrapped(*args, **kwargs):
                    result = run(*args, **kwargs)
                    record(name, result, True)
                    return result
                return wrapped

            gui_evidence = run_gui_live(
                terminal_run=capture("gui_terminal", terminal_tool.run),
                window_run=capture("gui_window", window_tool.run),
                desktop_run=capture("gui_desktop", desktop_tool.run),
            )
            records.append({"name": "gui_harness", "pass": gui_evidence["status"] == "PASS", "response": _bounded(gui_evidence)})

        payload = {
            "schema": "tools.v1.deep-validation/1",
            "timestamp_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "python": sys.version,
            "platform": sys.platform,
            "cases": records,
            "summary": {"total": len(records), "passed": sum(x["pass"] for x in records)},
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if payload["summary"]["passed"] == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
