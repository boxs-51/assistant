from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.capability import SkillDefinition
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.capabilities import RegistryAgentSkillResolver
from se.src.runtimes.agent.contracts.skills import ActiveSkillSet, SkillDescriptor
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityKind,
)
from se.src.runtimes.capability.contracts.skill_manifest import (
    SkillManifestV2,
    normalize_skill_manifest,
    trusted_skill_runtime_metadata,
)
from se.src.runtimes.capability.local_support_loader import LocalSupportLoader
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.transport.gateway.api.v1.capability_router import (
    _trusted_legacy_skill_metadata,
)


def _v2_manifest(**updates):
    manifest = {
        "schema_version": "2",
        "skill_id": "skill-docx",
        "version": "2.1.0",
        "name": "DOCX Workflow",
        "description": "Procedural DOCX guidance",
        "instruction": {"path": "instruction.md"},
        "activation": {
            "mode": "AUTO_ELIGIBLE",
            "intents": ["document", "docx"],
            "keywords": ["word", "docx"],
        },
        "capability_hints": [
            {
                "capability_id": "document.read",
                "purpose": "Inspect document content",
                "stage": "inspect",
                "required": False,
            }
        ],
        "references": [
            {
                "reference_id": "styles",
                "path": "references/styles.md",
                "description": "Style guidance",
                "activation_tags": ["style"],
            }
        ],
        "security": {
            "required_permissions": ["skill.docx"],
        },
    }
    manifest.update(updates)
    return manifest


def test_legacy_manifest_normalizes_to_v2_without_reinterpreting_identity():
    normalized = normalize_skill_manifest(
        {
            "name": "skill-web-research",
            "description": "Research workflow",
            "version": "1.0",
            "instruction_file": "instruction.md",
            "required_scopes": ["web.read"],
            "required_permissions": ["research.use"],
        }
    )

    assert normalized.schema_version == "2"
    assert normalized.skill_id == "skill-web-research"
    assert normalized.version == "1.0"
    assert normalized.instruction.path == "instruction.md"
    assert normalized.activation.mode.value == "ON_DEMAND"
    assert normalized.security.required_scopes == ("web.read",)
    assert normalized.security.required_permissions == ("research.use",)


@pytest.mark.parametrize(
    "bad_path",
    [
        "../other/secret.md",
        "/etc/passwd",
        "references/../../other/secret.md",
        "other-skill/references/secret.md",
        r"C:\other\secret.md",
    ],
)
def test_v2_reference_paths_fail_closed_outside_skill_reference_root(bad_path):
    manifest = _v2_manifest(
        references=[
            {
                "reference_id": "bad",
                "path": bad_path,
            }
        ]
    )

    with pytest.raises((ValidationError, ValueError)):
        SkillManifestV2.model_validate(manifest)


def test_v2_descriptor_identity_is_stable_and_empty_active_skill_set_is_valid():
    manifest = SkillManifestV2.model_validate(_v2_manifest())
    definition = CapabilityDefinition(
        id=manifest.skill_id,
        version=manifest.version,
        name=manifest.name,
        description=manifest.description,
        execution_kind="SKILL",
        kind=CapabilityKind.SKILL,
        metadata=trusted_skill_runtime_metadata(manifest),
    )

    descriptor = SkillDescriptor.from_definition(definition)

    assert descriptor.skill_id == "skill-docx"
    assert descriptor.version == "2.1.0"
    assert descriptor.capability_hints[0].capability_id == "document.read"
    assert descriptor.references[0].path == "references/styles.md"
    assert ActiveSkillSet().skills == ()


def test_v2_discovery_reads_manifest_only_and_hint_creates_no_tool_authority(tmp_path):
    skills_dir = tmp_path / "skills"
    agents_dir = tmp_path / "agents"
    package = skills_dir / "skill-docx"
    package.mkdir(parents=True)
    agents_dir.mkdir()
    (package / "manifest.json").write_text(
        json.dumps(_v2_manifest()),
        encoding="utf-8",
    )
    # instruction.md and references/styles.md intentionally do not exist.
    runtime = CapabilityRuntime(
        registry=CapabilityRegistry(),
        catalog=CapabilityCatalog(),
    )
    container = SimpleNamespace(
        capability_runtime=runtime,
        agent_registry=AgentRegistry(),
        agent_runtime=SimpleNamespace(),
        authorization_service=runtime.authorization,
    )
    loader = LocalSupportLoader(container, skills_dir, agents_dir)

    discovered = loader.discover()

    assert discovered["skills"] == ["skill-docx"]
    assert loader.is_loaded("skill-docx") is False
    definition = runtime.catalog.get_definition("skill-docx")
    assert definition.metadata["schema_version"] == "2"
    assert definition.metadata["loaded"] is False
    assert "instruction" not in definition.metadata
    assert runtime.catalog.contains_definition("document.read") is False


@pytest.mark.asyncio
async def test_unauthorized_skill_is_absent_from_descriptor_discovery():
    runtime = CapabilityRuntime(
        registry=CapabilityRegistry(),
        catalog=CapabilityCatalog(),
    )
    manifest = SkillManifestV2.model_validate(_v2_manifest())
    definition = CapabilityDefinition(
        id=manifest.skill_id,
        version=manifest.version,
        name=manifest.name,
        description=manifest.description,
        execution_kind="SKILL",
        kind=CapabilityKind.SKILL,
        metadata=trusted_skill_runtime_metadata(manifest),
    )
    runtime.catalog.register_definition(definition)
    resolver = RegistryAgentSkillResolver(
        agent_registry=AgentRegistry(),
        capability_catalog=runtime.catalog,
        authorization=runtime.authorization,
    )

    denied = await resolver.list_descriptors(identity=Identity(auth_type="guest"))
    allowed = await resolver.list_descriptors(
        identity=Identity(
            user_id="user-1",
            auth_type="jwt",
            permissions=["skill.docx"],
        )
    )

    assert denied == ()
    assert [(item.skill_id, item.version) for item in allowed] == [
        ("skill-docx", "2.1.0")
    ]


@pytest.mark.asyncio
async def test_unauthorized_assigned_skill_instruction_is_not_injected():
    agents = AgentRegistry()
    agents.register(
        AgentDefinition(
            name="reviewer",
            goal="Review",
            instruction="Review carefully.",
            skills=["private-skill"],
        )
    )
    catalog = CapabilityCatalog()
    catalog.register_definition(
        CapabilityDefinition(
            id="private-skill",
            name="private-skill",
            description="Private procedure",
            execution_kind="SKILL",
            kind=CapabilityKind.SKILL,
            metadata={
                "kind": "SKILL",
                "instruction": "PRIVATE INSTRUCTION",
                "required_permissions": ["skill.private"],
            },
        )
    )
    resolver = RegistryAgentSkillResolver(
        agent_registry=agents,
        capability_catalog=catalog,
    )

    denied = await resolver.resolve(
        agent_id="reviewer",
        identity=Identity(user_id="user-1", auth_type="jwt"),
    )
    allowed = await resolver.resolve(
        agent_id="reviewer",
        identity=Identity(
            user_id="user-1",
            auth_type="jwt",
            permissions=["skill.private"],
        ),
    )

    assert denied == ()
    assert [item.instruction for item in allowed] == ["PRIVATE INSTRUCTION"]


@pytest.mark.parametrize(
    "reserved_key",
    [
        "kind",
        "instruction",
        "server_managed",
        "lazy",
        "loaded",
        "schema_version",
        "activation",
        "capability_hints",
        "references",
        "provenance",
        "ownership",
    ],
)
def test_http_skill_registration_rejects_runtime_reserved_metadata(reserved_key):
    body = SkillDefinition(
        name="legacy-skill",
        description="Legacy compatibility",
        instruction="Do the legacy procedure.",
        metadata={reserved_key: "spoof"},
    )

    with pytest.raises(ValueError, match="runtime-reserved"):
        _trusted_legacy_skill_metadata(body)


def test_http_legacy_executable_skill_compatibility_metadata_is_preserved():
    body = SkillDefinition(
        name="legacy-skill",
        description="Legacy compatibility",
        instruction="Do the legacy procedure.",
        execution_mode="ONE_SHOT",
        metadata={
            "required_permissions": ["legacy.use"],
            "custom_label": "kept",
        },
    )

    metadata = _trusted_legacy_skill_metadata(body)

    assert body.execution_mode == "ONE_SHOT"
    assert metadata["required_permissions"] == ["legacy.use"]
    assert metadata["custom_label"] == "kept"
    assert metadata["server_managed"] is False
    assert metadata["loaded"] is True
    assert metadata["schema_version"] == "1"
    assert metadata["instruction"] == "Do the legacy procedure."
