from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from cl.src.loader.local_tools import LocalToolManager


T9_B_IDS = (
    "file.read",
    "file.search",
    "file.write",
    "file.append",
    "file.replace",
    "glob.find",
    "terminal.run",
    "terminal.launch",
)
PHYSICAL_ROOTS = ("file_tool", "find_by_glob", "terminal_tool")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_tools_config() -> dict:
    path = _repo_root() / "cl" / "config" / "setting.json"
    return json.loads(path.read_text(encoding="utf-8"))["tools_config"]


def test_default_client_placement_uses_exact_t9_b_ids_and_no_web():
    config = _default_tools_config()

    assert config["enabled_v2_capabilities"] == list(T9_B_IDS)
    assert "*" not in config["enabled_v2_capabilities"]
    assert not any(item.startswith("web.") for item in config["enabled_v2_capabilities"])
    assert not set(PHYSICAL_ROOTS).intersection(config["enabled_v2_capabilities"])


def test_real_top_level_t9_b_modules_load_as_logical_client_capabilities(tmp_path):
    root = _repo_root() / "tools" / "v1"
    loaded = LocalToolManager(root, set()).load_tools(_default_tools_config())

    assert set(T9_B_IDS).issubset(loaded)
    for physical_root in PHYSICAL_ROOTS:
        assert physical_root not in loaded
    assert not {"web.search", "web.read", "web.read_many"}.intersection(loaded)

    target = tmp_path / "append.txt"
    target.write_text("A", encoding="utf-8")
    append = loaded["file.append"]["func"]
    result = append(file_paths=str(target), content="B", encoding="utf-8")
    assert result["ok"] is True
    assert result["tool"] == "file_tool"
    assert result["action"] == "write"
    assert result["meta"]["version"] == "2.0.0"
    assert target.read_text(encoding="utf-8") == "AB"

    with pytest.raises(TypeError, match="immutable bound fields"):
        append(
            action="read",
            file_paths=str(target),
            content="C",
            encoding="utf-8",
        )

    found = loaded["glob.find"]["func"](
        pattern="*.txt",
        root_dir=str(tmp_path),
        recursive=False,
        max_results=10,
    )
    assert found["ok"] is True
    assert found["tool"] == "find_by_glob"
    assert found["action"] == "find"

    command_args = [sys.executable, "-c", "print('t9-b')"]
    command = (
        subprocess.list2cmdline(command_args)
        if os.name == "nt"
        else shlex.join(command_args)
    )
    terminal = loaded["terminal.run"]["func"](
        command=command,
        timeout=10,
        cwd=str(tmp_path),
        encoding="utf-8",
    )
    assert terminal["ok"] is True
    assert terminal["tool"] == "terminal_tool"
    assert terminal["action"] == "run"
    assert "t9-b" in terminal["data"]["stdout"]
