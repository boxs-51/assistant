import pytest

from se.src.context.source_identity import (
    ContextSourceKind,
    ContextSourceRef,
    context_source_id,
    create_context_source_ref,
    validate_context_source_ref_integrity,
)


def test_ctx_f2_session_identity_is_deterministic():
    first = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-1",
        owner_user_id="user-1",
    )
    second = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-1",
        owner_user_id="user-1",
        source_state="ACTIVE",
        metadata={"projection": "different"},
    )
    assert first.context_source_id == second.context_source_id


@pytest.mark.parametrize("kind", [ContextSourceKind.TASK, ContextSourceKind.BRANCH])
def test_ctx_f2_revisioned_agent_source_changes_identity(kind):
    first = create_context_source_ref(
        source_kind=kind,
        authority_id="native-1",
        authority_version=1,
        owner_user_id="user-1",
    )
    second = create_context_source_ref(
        source_kind=kind,
        authority_id="native-1",
        authority_version=2,
        owner_user_id="user-1",
    )
    assert first.context_source_id != second.context_source_id


def test_ctx_f2_agent_transcript_version_is_exact_identity_evidence():
    first = create_context_source_ref(
        source_kind=ContextSourceKind.AGENT_TRANSCRIPT,
        authority_id="transcript-ref",
        authority_version=3,
        owner_user_id="user-1",
    )
    replay = create_context_source_ref(
        source_kind=ContextSourceKind.AGENT_TRANSCRIPT,
        authority_id="transcript-ref",
        authority_version=3,
        owner_user_id="user-1",
    )
    next_version = create_context_source_ref(
        source_kind=ContextSourceKind.AGENT_TRANSCRIPT,
        authority_id="transcript-ref",
        authority_version=4,
        owner_user_id="user-1",
    )
    assert first.context_source_id == replay.context_source_id
    assert first.context_source_id != next_version.context_source_id


def test_ctx_f2_asset_identity_uses_asset_id_without_lifecycle_revision():
    first = create_context_source_ref(
        source_kind=ContextSourceKind.ASSET,
        authority_id="asset-1",
        owner_user_id="user-1",
        metadata={"file_asset_revision": 1},
    )
    lifecycle_changed = create_context_source_ref(
        source_kind=ContextSourceKind.ASSET,
        authority_id="asset-1",
        owner_user_id="user-1",
        metadata={"file_asset_revision": 2},
    )
    assert first.context_source_id == lifecycle_changed.context_source_id

    with pytest.raises(ValueError, match="does not use authority_version"):
        create_context_source_ref(
            source_kind=ContextSourceKind.ASSET,
            authority_id="asset-1",
            authority_version=2,
            owner_user_id="user-1",
        )


def test_ctx_f2_tool_response_payload_identity_uses_payload_id_only():
    first = create_context_source_ref(
        source_kind=ContextSourceKind.TOOL_RESPONSE_PAYLOAD,
        authority_id="payload-1",
        owner_user_id="user-1",
    )
    second = create_context_source_ref(
        source_kind=ContextSourceKind.TOOL_RESPONSE_PAYLOAD,
        authority_id="payload-1",
        owner_user_id="user-1",
        session_id="session-2",
    )
    assert first.context_source_id == second.context_source_id


def test_ctx_f2_same_textual_authority_across_source_kinds_is_distinct():
    session = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="same-id",
        owner_user_id="user-1",
    )
    asset = create_context_source_ref(
        source_kind=ContextSourceKind.ASSET,
        authority_id="same-id",
        owner_user_id="user-1",
    )
    assert session.context_source_id != asset.context_source_id


def test_ctx_f2_owner_scope_is_part_of_projection_identity():
    left = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-1",
        owner_user_id="user-1",
    )
    right = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-1",
        owner_user_id="user-2",
    )
    assert left.context_source_id != right.context_source_id


@pytest.mark.parametrize("field,value", [
    ("owner_user_id", ""),
    ("authority_id", "   "),
])
def test_ctx_f2_empty_owner_or_authority_is_rejected(field, value):
    kwargs = {
        "source_kind": ContextSourceKind.SESSION,
        "authority_id": "session-1",
        "owner_user_id": "user-1",
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match="must be non-empty"):
        create_context_source_ref(**kwargs)


@pytest.mark.parametrize("authority_id", [
    "https://example.test/file",
    "file:///tmp/file",
    "s3://bucket/key",
    "gs://bucket/key",
    "provider://file-id",
    "/tmp/file",
    "../relative-file",
    "bucket/object-key",
    "folder/local-file",
    "C:/temp/file",
    r"C:\\temp\\file",
])
def test_ctx_f2_url_and_path_values_are_not_native_authorities(authority_id):
    with pytest.raises(ValueError, match="native logical authority"):
        create_context_source_ref(
            source_kind=ContextSourceKind.ASSET,
            authority_id=authority_id,
            owner_user_id="user-1",
        )


@pytest.mark.parametrize(
    "kind",
    [
        ContextSourceKind.TASK,
        ContextSourceKind.BRANCH,
        ContextSourceKind.AGENT_TRANSCRIPT,
    ],
)
def test_ctx_f2_versioned_sources_require_nonnegative_authority_version(kind):
    with pytest.raises(ValueError, match="requires authority_version"):
        create_context_source_ref(
            source_kind=kind,
            authority_id="native-1",
            owner_user_id="user-1",
        )
    with pytest.raises(ValueError, match="must be >= 0"):
        create_context_source_ref(
            source_kind=kind,
            authority_id="native-1",
            authority_version=-1,
            owner_user_id="user-1",
        )


def test_ctx_f2_projection_metadata_is_deeply_immutable_and_not_identity_authority():
    ref = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-1",
        owner_user_id="user-1",
        metadata={"nested": [{"value": 1}]},
    )
    with pytest.raises(TypeError):
        ref.metadata["nested"][0]["value"] = 2

    dumped = ref.model_dump(mode="json")
    assert dumped["metadata"] == {"nested": [{"value": 1}]}


def test_ctx_f2_model_copy_identity_fields_must_remain_canonical():
    valid = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-1",
        owner_user_id="user-1",
    )
    forged_owner = valid.model_copy(update={"owner_user_id": " user-1 "})
    forged_authority = valid.model_copy(update={"authority_id": " session-1 "})

    with pytest.raises(ValueError, match="owner_user_id must already be"):
        validate_context_source_ref_integrity(forged_owner)

    with pytest.raises(ValueError, match="authority_id must already be"):
        validate_context_source_ref_integrity(forged_authority)


def test_ctx_f2_model_copy_cannot_forge_context_source_id():
    valid = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-1",
        owner_user_id="user-1",
    )
    forged = valid.model_copy(update={"context_source_id": "forged"})

    with pytest.raises(ValueError, match="does not match"):
        validate_context_source_ref_integrity(forged)

    data = forged.model_dump(mode="json")
    with pytest.raises(ValueError, match="does not match"):
        ContextSourceRef(**data)


def test_ctx_f2_model_copy_cannot_smuggle_mutable_metadata_descendants():
    valid = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-1",
        owner_user_id="user-1",
    )
    forged = valid.model_copy(
        update={"metadata": {"nested": [{"value": 1}]}}
    )

    with pytest.raises(ValueError, match="metadata root must be a frozen JSON object"):
        validate_context_source_ref_integrity(forged)


def test_ctx_f2_extra_provider_alias_fields_are_forbidden():
    valid_id = context_source_id(
        source_kind=ContextSourceKind.ASSET,
        authority_id="asset-1",
        owner_user_id="user-1",
    )
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        ContextSourceRef(
            context_source_id=valid_id,
            source_kind=ContextSourceKind.ASSET,
            authority_id="asset-1",
            owner_user_id="user-1",
            provider_file_id="provider-123",
        )


def test_ctx_f2_integrity_rejects_fractional_and_boolean_authority_version():
    valid = create_context_source_ref(
        source_kind=ContextSourceKind.TASK,
        authority_id="task-1",
        authority_version=1,
        owner_user_id="user-1",
    )

    forged_fractional = valid.model_copy(
        update={
            "authority_version": 1.5,
            "context_source_id": "forged",
        }
    )
    with pytest.raises(ValueError, match="authority_version must be an integer"):
        validate_context_source_ref_integrity(forged_fractional)

    forged_bool = valid.model_copy(
        update={
            "authority_version": True,
            "context_source_id": "forged",
        }
    )
    with pytest.raises(ValueError, match="authority_version must be an integer"):
        validate_context_source_ref_integrity(forged_bool)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("session_id", {"mutable": []}, "session_id must be a string or None"),
        ("task_id", ["bad"], "task_id must be a string or None"),
        ("branch_id", {"bad": 1}, "branch_id must be a string or None"),
        ("source_state", ["ACTIVE"], "source_state must be a string or None"),
        ("source_created_at", {"bad": "date"}, "source_created_at must be a datetime or None"),
    ],
)
def test_ctx_f2_integrity_rejects_wrong_typed_projection_fields(field, value, message):
    valid = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-1",
        owner_user_id="user-1",
    )
    forged = valid.model_copy(update={field: value})

    with pytest.raises(ValueError, match=message):
        validate_context_source_ref_integrity(forged)


def test_ctx_f2_integrity_rejects_unvalidated_source_kind():
    valid = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-1",
        owner_user_id="user-1",
    )
    forged = valid.model_copy(update={"source_kind": "SESSION"})

    with pytest.raises(ValueError, match="source_kind must be a ContextSourceKind"):
        validate_context_source_ref_integrity(forged)


def test_ctx_f2_integrity_rejects_non_object_frozen_metadata_root():
    valid = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-1",
        owner_user_id="user-1",
    )
    forged = valid.model_copy(update={"metadata": ()})

    with pytest.raises(ValueError, match="metadata root must be a frozen JSON object"):
        validate_context_source_ref_integrity(forged)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("owner_user_id", {"bad": "owner"}, "owner_user_id must be a string"),
        ("authority_id", ["bad-authority"], "authority_id must be a string"),
    ],
)
def test_ctx_f2_integrity_rejects_non_string_identity_fields(field, value, message):
    valid = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-1",
        owner_user_id="user-1",
    )
    forged = valid.model_copy(update={field: value})

    with pytest.raises(ValueError, match=message):
        validate_context_source_ref_integrity(forged)


def test_ctx_f2_integrity_rejects_model_copy_extra_provider_alias():
    valid = create_context_source_ref(
        source_kind=ContextSourceKind.ASSET,
        authority_id="asset-1",
        owner_user_id="user-1",
    )
    forged = valid.model_copy(
        update={"provider_file_id": {"mutable": []}}
    )

    with pytest.raises(ValueError, match="undeclared stored fields: provider_file_id"):
        validate_context_source_ref_integrity(forged)
