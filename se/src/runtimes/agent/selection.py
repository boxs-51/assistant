"""Deterministic conservative DCS-1 selector."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .contracts.selection import (
    CapabilitySelectionContext,
    CapabilitySelectionResult,
    CapabilityWorkingSet,
)

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "use",
        "using",
        "tool",
        "tools",
        "capability",
        "capabilities",
        "request",
        "current",
        "available",
        "one",
        "into",
        "only",
        "when",
        "where",
        "what",
        "which",
        "then",
        "than",
        "your",
        "you",
    }
)


class CapabilitySelectionViolationError(RuntimeError):
    code = "DCS_TOOL_NOT_SELECTED"
    failure_domain = "DCS"
    retryable = False

    def __init__(self, capability_ids: Sequence[str]) -> None:
        rejected = tuple(dict.fromkeys(str(item) for item in capability_ids))
        self.capability_ids = rejected
        super().__init__(
            "Model requested capability outside current DCS working set: "
            + ", ".join(rejected)
        )


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, (tuple, list, set, frozenset)):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def _words(value: str) -> set[str]:
    return {
        word
        for word in _WORD_RE.findall(value.lower())
        if len(word) >= 3 and word not in _STOP_WORDS
    }


def _conversation_signal(context: CapabilitySelectionContext) -> tuple[str, set[str]]:
    fragments: list[str] = []
    prior_tool_ids: set[str] = set()
    for message in context.messages:
        if message.role != "system":
            fragments.append(_flatten_text(message.content))
        if message.role == "tool" and message.name:
            prior_tool_ids.add(message.name)
        for tool_call in message.tool_calls:
            prior_tool_ids.add(tool_call.name)
    return " ".join(fragments).lower(), prior_tool_ids


def _candidate_matches(text: str, text_words: set[str], candidate) -> bool:
    capability_id = candidate.capability_id.lower()
    name = candidate.name.lower()
    if capability_id in text or name in text:
        return True

    identity_words = _words(
        " ".join(
            (
                capability_id.replace(".", " ").replace("_", " ").replace("-", " "),
                name.replace(".", " ").replace("_", " ").replace("-", " "),
            )
        )
    )
    if identity_words.intersection(text_words):
        return True

    description_words = _words(candidate.description)
    return bool(description_words.intersection(text_words))


class DeterministicCapabilitySelector:
    """DCS-1 rule selector.

    The selector consumes only candidates that have already passed the Agent
    envelope, authorization and availability/routability gates.  It never adds
    capabilities, never consumes Skill capability_hints and has no all-tools
    uncertainty fallback.  DCS-2 owns ranking/grouping/expansion.
    """

    def select(
        self,
        context: CapabilitySelectionContext,
    ) -> CapabilitySelectionResult:
        text, prior_tool_ids = _conversation_signal(context)
        text_words = _words(text)
        explicitly_requested = set(context.explicit_requested_capability_ids)

        visible: list[str] = []
        provenance: list[str] = []
        for candidate in context.eligible_capabilities:
            capability_id = candidate.capability_id
            if capability_id in explicitly_requested:
                visible.append(capability_id)
                provenance.append(f"explicit:{capability_id}")
                continue
            if capability_id in prior_tool_ids:
                visible.append(capability_id)
                provenance.append(f"prior-tool:{capability_id}")
                continue
            if _candidate_matches(text, text_words, candidate):
                visible.append(capability_id)
                provenance.append(f"task-match:{capability_id}")

        working_set = CapabilityWorkingSet(
            visible_capability_ids=tuple(visible),
            active_groups=(),
            reason=(
                "DCS1_DETERMINISTIC_TASK_MATCH"
                if visible
                else "DCS1_ZERO_TOOL_FAST_PATH"
            ),
            provenance=tuple(provenance),
            revision=context.iteration,
        )
        return CapabilitySelectionResult(
            working_set=working_set,
            active_skill_set=context.active_skill_set,
        )


def ensure_selected_tool_calls(
    selected_tool_names: Sequence[str],
    requested_tool_names: Sequence[str],
) -> None:
    """Fail closed before dispatch when the model calls an unselected Tool."""
    selected = set(selected_tool_names)
    rejected = [
        name
        for name in requested_tool_names
        if name not in selected
    ]
    if rejected:
        raise CapabilitySelectionViolationError(rejected)


__all__ = [
    "CapabilitySelectionViolationError",
    "DeterministicCapabilitySelector",
    "ensure_selected_tool_calls",
]
