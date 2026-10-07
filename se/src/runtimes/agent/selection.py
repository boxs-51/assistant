"""Deterministic bounded DCS-2 capability selector."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .contracts.selection import (
    CapabilitySelectionContext,
    CapabilitySelectionResult,
    CapabilityWorkingSet,
)
from .contracts.skills import ActiveSkill, ActiveSkillSet, SkillActivationSource

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


def select_active_assigned_skills(
    assigned_skill_set: ActiveSkillSet,
    messages: Sequence[Any],
    *,
    explicit_requested_skill_ids: Sequence[str] = (),
) -> ActiveSkillSet:
    """Activate only relevant assigned Skills while preserving legacy preload.

    Canonical V2 activation metadata controls trusted descriptors.  Legacy
    assigned/preloaded Skills retain historical eager behavior until SKV2-X1.
    Capability hints are deliberately ignored here.
    """
    text = " ".join(
        _flatten_text(getattr(message, "content", ""))
        for message in messages
        if getattr(message, "role", None) != "system"
    ).lower()
    text_words = _words(text)
    explicitly_requested = set(explicit_requested_skill_ids)
    active: list[ActiveSkill] = []

    for item in assigned_skill_set.skills:
        descriptor = item.descriptor
        if descriptor.provenance == "LEGACY_ASSIGNED":
            active.append(item)
            continue

        mode = descriptor.activation.mode.value
        if mode == "ALWAYS_ON":
            source = SkillActivationSource.ALWAYS_ON
        elif mode == "ON_DEMAND":
            if descriptor.skill_id not in explicitly_requested:
                continue
            source = SkillActivationSource.ON_DEMAND
        elif mode == "AUTO_ELIGIBLE":
            terms = (
                *descriptor.activation.intents,
                *descriptor.activation.keywords,
            )
            relevant = False
            for term in terms:
                normalized = term.strip().lower()
                if not normalized:
                    continue
                if normalized in text or _words(normalized).intersection(text_words):
                    relevant = True
                    break
            if not relevant:
                continue
            source = SkillActivationSource.AUTO_MATCH
        else:
            continue

        active.append(
            ActiveSkill(
                descriptor=descriptor,
                source=source,
            )
        )

    return ActiveSkillSet(skills=tuple(active))



def _capability_group(capability_id: str) -> str:
    """Return deterministic metadata-only group identity for a logical capability."""
    normalized = capability_id.strip().lower()
    if not normalized:
        return ""
    return normalized.split(".", 1)[0]


def _relevant_skill_hint_ids(
    context: CapabilitySelectionContext,
    *,
    text: str,
    text_words: set[str],
) -> set[str]:
    """Return only trusted, task-relevant hints that target eligible capabilities."""
    eligible_ids = {
        candidate.capability_id
        for candidate in context.eligible_capabilities
    }
    hinted: set[str] = set()
    for active_skill in context.active_skill_set.skills:
        for hint in active_skill.descriptor.capability_hints:
            capability_id = hint.capability_id
            if capability_id not in eligible_ids:
                continue
            purpose = hint.purpose.strip().lower()
            if not purpose:
                continue
            if purpose in text or _words(purpose).intersection(text_words):
                hinted.add(capability_id)
    return hinted


class DeterministicCapabilitySelector:
    """Bounded deterministic DCS-2 selector.

    Candidates have already passed the Agent envelope, authorization and
    availability/routability gates. Grouping and Skill hints are metadata-only
    ranking signals and can never add a candidate outside that eligible set.
    """

    def __init__(self, *, max_visible: int = 8) -> None:
        if type(max_visible) is not int or max_visible < 1:
            raise ValueError("max_visible must be a positive integer.")
        self._max_visible = max_visible

    def select(
        self,
        context: CapabilitySelectionContext,
    ) -> CapabilitySelectionResult:
        text, prior_tool_ids = _conversation_signal(context)
        text_words = _words(text)
        explicitly_requested = set(context.explicit_requested_capability_ids)

        eligible_ids = {
            candidate.capability_id
            for candidate in context.eligible_capabilities
        }
        prior_eligible_ids = {
            capability_id
            for capability_id in prior_tool_ids
            if capability_id in eligible_ids
        }
        expansion_groups = {
            _capability_group(capability_id)
            for capability_id in prior_eligible_ids
            if _capability_group(capability_id)
        }
        hinted_ids = _relevant_skill_hint_ids(
            context,
            text=text,
            text_words=text_words,
        )

        ranked: list[tuple[int, int, str, tuple[str, ...]]] = []
        for index, candidate in enumerate(context.eligible_capabilities):
            capability_id = candidate.capability_id
            group_id = _capability_group(capability_id)
            score = 0
            signals: list[str] = []

            if capability_id in explicitly_requested:
                score += 1000
                signals.append(f"explicit:{capability_id}")
            if capability_id in prior_eligible_ids:
                score += 800
                signals.append(f"prior-tool:{capability_id}")
            if _candidate_matches(text, text_words, candidate):
                score += 500
                signals.append(f"task-match:{capability_id}")
            if capability_id in hinted_ids:
                score += 300
                signals.append(f"skill-hint:{capability_id}")
            if (
                group_id
                and group_id in expansion_groups
                and capability_id not in prior_eligible_ids
            ):
                score += 200
                signals.append(f"group-expand:{group_id}:{capability_id}")

            if score <= 0:
                continue
            ranked.append((-score, index, capability_id, tuple(signals)))

        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        selected = ranked[: self._max_visible]
        visible = tuple(item[2] for item in selected)

        provenance: list[str] = []
        active_groups: list[str] = []
        for _, _, capability_id, signals in selected:
            provenance.extend(signals)
            group_id = _capability_group(capability_id)
            if group_id and group_id not in active_groups:
                active_groups.append(group_id)

        working_set = CapabilityWorkingSet(
            visible_capability_ids=visible,
            active_groups=tuple(active_groups),
            reason=(
                "DCS2_BOUNDED_RANKED_SELECTION"
                if visible
                else "DCS2_ZERO_TOOL_FAST_PATH"
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
    "select_active_assigned_skills",
]
