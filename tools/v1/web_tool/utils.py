from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit


def clean_whitespace(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[\xa0\u1680\u180e\u2000-\u200b\u202f\u205f\u3000]", " ", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def deduplicate_content(text: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    # Some sites render the same compact summary card twice with different
    # headings. Remove nearby repeated runs while preserving dated day cards.
    duplicate_lines: set[int] = set()
    recent_runs: dict[tuple[str, ...], int] = {}
    run_width = 6
    for index in range(max(0, len(lines) - run_width + 1)):
        run = tuple(line.strip() for line in lines[index:index + run_width])
        if any(not line or line.startswith("#") for line in run):
            continue
        if sum(len(line) for line in run) < 30:
            continue
        previous = recent_runs.get(run)
        if previous is not None and run_width <= index - previous <= 20:
            duplicate_lines.update(range(index, index + run_width))
        else:
            recent_runs[run] = index
    seen: set[str] = set()
    output: list[str] = []
    for index, line in enumerate(lines):
        if index in duplicate_lines:
            continue
        stripped = line.strip()
        if stripped and len(stripped) > 15:
            if stripped in seen:
                continue
            seen.add(stripped)
        output.append(line)
    return "\n".join(output)


def clean_ddg_url(raw_url: str) -> str:
    if not isinstance(raw_url, str):
        return ""
    raw_url = raw_url.rstrip(")").strip()
    if "/l/?" in raw_url or "uddg=" in raw_url:
        try:
            parsed = urlsplit(raw_url)
            values = parse_qs(parsed.query).get("uddg")
            if values:
                raw_url = unquote(values[0]).rstrip(")").strip()
        except Exception:
            return ""
    return raw_url


def public_result_url(raw_url: str, max_length: int) -> str:
    if not isinstance(raw_url, str):
        return ""
    value = clean_ddg_url(raw_url)
    if not value or len(value) > max_length:
        return ""
    try:
        parts = urlsplit(value)
    except Exception:
        return ""
    if parts.scheme.lower() not in {"http", "https"}:
        return ""
    if parts.username is not None or parts.password is not None or not parts.hostname:
        return ""
    try:
        port = parts.port
    except ValueError:
        return ""
    host = parts.hostname.lower().rstrip(".")
    host_netloc = f"[{host}]" if ":" in host and not host.startswith("[") else host
    default_port = 443 if parts.scheme.lower() == "https" else 80
    if port is not None and port != default_port:
        host_netloc = f"{host_netloc}:{port}"
    return urlunsplit((parts.scheme.lower(), host_netloc, parts.path or "/", parts.query, ""))
