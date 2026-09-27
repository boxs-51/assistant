# CAS-F6-0 — Client/UI Unified Asset Flow Contract Freeze

Status: **CONTRACT / PRE-CLAIM / ARCHITECTURE-EVIDENCE CANDIDATE**

Primary workspace: Issue #74  
Canonical governance: Issue #85 v2.5  
Auditor release: Issue #74 comment #5852121203  
Baseline: `main@540900f950f4e7826a3a24f4a2e657c175ce818b`

## 1. Authority and scope

This document freezes the **CAS-F6-0 client/UI unified asset-flow contract** only.

```text
production/runtime/schema/migration delta = ZERO
CAS-F6 production implementation authority = CLOSED
CAS-F7 = CLOSED
CAS-F8 = CLOSED
merge authority = NONE
```

CAS-F5 is already COMPLETE / CANONICAL / HEALTHY. F6 starts from that landed provider-hydration boundary and does not redefine it.

F6 is specifically the client/UI convergence from local user-selected content into canonical CAS identity.

F6 is not an umbrella for provider-generated media, legacy cutover, deletion, cleanup, recovery or physical GC.

## 2. Canonical entry state

At this contract baseline:

```text
canonical main = 540900f950f4e7826a3a24f4a2e657c175ce818b
CAS-F5 exit PR #114 = MERGED
post-wave Architecture #1412 = GREEN/GREEN
linux-full-suite = SUCCESS
windows-client-contracts = SUCCESS
CAS-F5 initial provider-hydration milestone = COMPLETE
blocking inherited CAS-F5 P0/P1/P2 = NONE
```

The landed server already owns canonical CAS ingestion and content access.

Canonical authenticated HTTP surfaces:

```text
POST /v1/assets
GET  /v1/assets
GET  /v1/assets/{asset_id}
GET  /v1/assets/{asset_id}/content
```

The server derives `owner_user_id` from authenticated `Identity`; the client does not provide owner authority.

The canonical server/client attachment shape already supports:

```text
asset_id = <canonical asset id>
source   = "asset"
uri      = asset://<asset_id>
```

Canonical asset attachments cannot carry provider file identity, inline base64 or inline bytes at the same time.

## 3. Current implementation facts and convergence gaps

These are baseline observations used to define the F6 implementation target. They are historical entry facts, not permanent live-source absence invariants.

### Python client

`cl/src/core/gateway_client.py` currently exposes provider-specific legacy `/v1/files` methods and has not yet converged the user-upload path onto canonical `/v1/assets`.

`cl/src/core/content_processor.py` currently converts local files to base64/data-URI request content instead of first producing a canonical Asset descriptor.

### Canonical attachment schema

`cl/src/schemas/attachment.py` already contains the positive canonical shape needed by F6:

- `asset_id`;
- `source="asset"`;
- `uri="asset://<asset_id>"`;
- canonical asset/provider/base64/bytes mixing rejection.

No schema change is expected for the first F6 production slice unless a later exact implementation audit proves a parity gap.

### Web UI

`cl/src/ui/web/js/components/console/normalizer.js` does not yet guarantee preservation of canonical `asset_id` authority through every normalized attachment path.

Current file/image/media renderers derive preview/download sources from `attachment.uri` or inline data.

That is incompatible with canonical browser behavior because `asset://<asset_id>` is identity, not a browser-fetch URL.

These current gaps are implementation targets for a later production slice. This F6-0 contract must not encode tests that require those gaps to remain present forever.

## 4. Canonical client submission flow

The F6 canonical submission path is:

```text
user-selected local file / bytes
    -> authenticated POST /v1/assets
    -> owner-authorized READY AssetDescriptor
    -> GatewayAttachment(
         asset_id=<descriptor.asset_id>,
         source="asset",
         uri="asset://<descriptor.asset_id>",
         filename=<descriptor.filename>,
         mime_type=<descriptor.mime_type>,
         size=<descriptor.size_bytes>
       )
    -> ordinary DIRECT / AGENT chat request
    -> already-landed CAS-F5 hydration / provider projection
```

Only a successfully persisted READY canonical asset may be inserted into the canonical chat request.

The canonical request must not persist the upload's local path, raw bytes, base64 payload, provider file ID, provider URI, object-store key, signed URL or ephemeral browser object URL as asset identity.

## 5. AssetDescriptor -> GatewayAttachment mapping

The client/UI mapping is frozen as follows:

| AssetDescriptor field | Canonical GatewayAttachment field |
|---|---|
| `asset_id` | `asset_id` |
| `asset_id` | `uri = asset://<asset_id>` |
| canonical source | `source = "asset"` |
| `filename` | `filename` |
| `mime_type` | `mime_type` |
| `size_bytes` | `size` |

For this canonical attachment:

```text
provider_file_id = null
base64_data = null
bytes_data = null
```

No client-supplied `owner_user_id`, user ID or equivalent field grants asset access.

Organization/user authorization remains a server-owned concern derived from authenticated identity.

## 6. Client upload state machine

Per selected file:

```text
SELECTED
   -> UPLOADING_CANONICAL
      -> READY_CANONICAL
         -> ATTACHED_TO_DRAFT
            -> SENT
      -> UPLOAD_FAILED
```

Rules:

1. `READY_CANONICAL` requires a successful `POST /v1/assets` response representing a READY asset descriptor.
2. Upload failure is fail-closed for the canonical send path.
3. A failed canonical upload MUST NOT automatically fall back to `/v1/files`.
4. A failed upload MUST NOT insert raw bytes/base64/provider identity into the request as an implicit fallback.
5. Retrying a failed file is an explicit canonical upload retry.
6. A previously successful READY descriptor may be reused by the current draft without re-uploading solely because another file failed.
7. F6 does not gain destructive cleanup authority from upload failure.

## 7. Multi-file partial-failure contract

For a user action selecting multiple files, each file has an independently visible upload result.

A batch may therefore reach:

```text
all READY
partial READY + partial FAILED
all FAILED
```

Only READY canonical descriptors are eligible to enter the chat request.

If any selected file fails:

- the client/UI MUST surface which files succeeded and which failed;
- the client/UI MUST NOT silently auto-send a reduced subset;
- the user may explicitly retry failed uploads;
- the user may explicitly continue with the successfully persisted subset;
- successful canonical assets remain available to that draft;
- no implicit provider upload fallback is allowed;
- no implicit deletion/reclamation of already-created assets or provider bindings is allowed.

The explicit user continuation step prevents a partial upload from silently changing the intended attachment set.

## 8. Canonical metadata/list flow

Canonical client/UI metadata flows use:

```text
GET /v1/assets
GET /v1/assets/{asset_id}
```

Bearer/authenticated identity remains the only access authority.

Client state may retain display metadata such as:

```text
asset_id
source="asset"
uri="asset://<asset_id>"
filename
mime_type
size
```

Provider identity must not enter canonical message or asset state.

## 9. Preview, view and download contract

`asset://<asset_id>` is a canonical identity URI only.

It MUST NOT be passed directly to:

- browser `fetch`;
- `<img src>`;
- `<audio src>`;
- `<video src>`;
- `window.open`;
- file download helpers;
- editor/file viewers as though it were a browser-resolvable URL.

Canonical content resolution is:

```text
GatewayAttachment.asset_id
   -> authenticated GET /v1/assets/{asset_id}/content
   -> response bytes / stream
   -> optional ephemeral in-browser object URL
   -> preview / view / download
```

The authenticated content request uses the same client authentication authority as other gateway calls.

If the UI creates an object URL:

- it is transport/rendering state only;
- it must not replace `asset_id` or `asset://...` in canonical state;
- it must not be persisted in message/session history;
- it should be revoked when no longer needed.

Range reads may be used for media/document UX because the server content endpoint already supports byte ranges.

## 10. UI normalization contract

Canonical UI normalization must preserve, when present:

```text
asset_id
source="asset"
uri="asset://<asset_id>"
filename
mime_type
size
```

Normalization must not derive a local/provider identity that overrides canonical `asset_id`.

For canonical attachments, `asset_id` is authoritative and `uri` is canonical identity metadata.

Renderer-specific browser URLs are derived ephemerally after authenticated content resolution and are not written back into the normalized canonical attachment.

## 11. Python client surface contract

The active desktop Web UI submission ownership chain at this baseline is:

```text
cl/src/ui/web/js/app.js
  -> cl/src/ui/web/js/components/inputFrame.js
  -> cl/src/ui/web/js/components/inputFrame/fileManager.js
     -> current baseline preparation:
        window.pywebview.api.encode_files_async(...)
        -> cl/src/ui/bridge.py::UIBridge.encode_files_async
        -> cl/src/ui/encoder.py::FileEncoder.encode_async/_worker
        -> local bytes -> base64 payload -> onFileEncodeComplete(...)
  -> window.pywebview.api.submit_prompt(...)
  -> cl/src/ui/bridge.py::UIBridge.submit_prompt
  -> ONLINE: canonical /v1/assets upload result -> GatewayAttachment -> GatewayChatRequest / client runtime
     LOCAL_OFFLINE: retain FileEncoder/base64 compatibility
                    -> cl/src/core/agent_engine.py::run_agent_session
                    -> process_attached_files(...)
```

The first production slice must own this chain coherently enough to implement canonical upload, per-file READY/FAILED state, explicit retry/continue semantics and canonical attachment submission without stepping outside its declared file matrix.

The F6 production split is frozen as follows:

- **ONLINE canonical DIRECT/AGENT path:** selected local files MUST be uploaded through authenticated `POST /v1/assets`; canonical send state is built from READY AssetDescriptor/GatewayAttachment values. The ONLINE canonical path MUST bypass the legacy `FileEncoder` base64 transformation and MUST NOT persist or submit its `b64_data` as canonical attachment identity.
- **LOCAL_OFFLINE compatibility path:** `cl/src/ui/encoder.py::FileEncoder` remains the existing local/base64 preparation mechanism unless a later separately released stage changes it. F6 does not require changing offline base64 behavior.
- `cl/src/core/agent_engine.py::AgentEngine.run_agent_session` and `cl/src/core/content_processor.py::process_attached_files` remain LOCAL_OFFLINE compatibility owners for this first ONLINE canonical slice.
- The ONLINE desktop canonical path MUST construct canonical GatewayAttachment/request state in the UI bridge/client path and MUST NOT route attachment preparation through `AgentEngine.run_agent_session(... attached_files=...)` or `process_attached_files()`.
- Therefore `cl/src/ui/encoder.py`, `cl/src/core/agent_engine.py`, and `cl/src/core/content_processor.py` are active baseline compatibility owners but are classified `LOCAL_OFFLINE COMPATIBILITY / EXPECT NO CHANGE` for the first F6 production slice. Client/UI wiring around them may change so ONLINE bypasses that base64 path while LOCAL_OFFLINE continues to use it unchanged.
- If implementation proves that any of those LOCAL_OFFLINE compatibility files must change rather than be bypassed for ONLINE, the production CLAIM must re-audit that scope before editing them.

The first production slice is expected to add canonical asset operations to `GatewayLLMClient` while retaining legacy file methods in parallel.

Behavioral surface to implement:

```text
upload canonical asset
list canonical assets
get canonical asset metadata
read/download canonical asset content
optional Range support for content read
```

Exact Python method names/signatures may be finalized by the production CLAIM, but all methods must target `/v1/assets` and use existing authenticated request handling.

The canonical upload method must use multipart upload and return the server AssetDescriptor payload.

No canonical method accepts `provider_name` as canonical routing authority.

## 12. Legacy /v1/files boundary

F6 may coexist with existing provider-specific `/v1/files` client methods.

F6 MUST NOT:

- delete those methods;
- redirect them silently to `/v1/assets`;
- reinterpret legacy provider file IDs as canonical asset IDs;
- automatically fall back from a failed `/v1/assets` upload to `/v1/files`;
- claim legacy attachment cutover/backfill/removal.

Legacy cutover/removal belongs to CAS-F8.

The existence of legacy methods during F6 is compatibility, not canonical authority.

## 13. CAS-F5 handoff boundary

F6 prepares canonical client/UI input only.

After the canonical `GatewayAttachment` enters a normal DIRECT/AGENT request, the already-landed F5 runtime owns provider hydration/projection.

F6 does not redefine:

- exact provider selection;
- provider namespace;
- `ProviderCallBudget`;
- provider retry/fallback/deadline;
- hydration terminal/fallback semantics;
- provider projection;
- canonical history neutrality.

Streaming and non-stream chat behavior remain owned by existing chat/F5 runtime.

## 14. Error and retry behavior

Canonical client operations surface server failures explicitly.

Relevant classes include:

- authentication failure;
- upload size rejection;
- non-READY/state conflict;
- not-found/unauthorized-as-not-found;
- storage/backend unavailability;
- network/transport failure.

Rules:

1. No failure class authorizes provider-file fallback.
2. No failure class authorizes implicit destructive cleanup.
3. Retry remains a canonical `/v1/assets` operation.
4. A known READY descriptor should not be discarded merely because a separate file operation failed.
5. Content preview/download failure must not mutate canonical attachment identity.

## 15. First F6 production-slice ownership matrix

This is the candidate matrix for the first production CLAIM. It is not a production ownership grant.

| Path | Expected role | Initial disposition |
|---|---|---|
| `cl/src/core/gateway_client.py` | canonical /v1/assets client methods | EXPECTED CHANGE |
| `cl/src/core/content_processor.py` | LOCAL_OFFLINE `process_attached_files` compatibility owner; not on first ONLINE canonical desktop path | LOCAL_OFFLINE COMPATIBILITY / EXPECT NO CHANGE |
| `cl/src/ui/web/js/app.js` | application submit caller that bridges input-frame payloads to `UIBridge.submit_prompt` | EXPECTED CHANGE / WIRING OWNER |
| `cl/src/ui/web/js/components/inputFrame.js` | selected-file queue submission, send gating, retry/restore orchestration | EXPECTED CHANGE |
| `cl/src/ui/web/js/components/inputFrame/fileManager.js` | per-file upload state, progress, READY/FAILED payload ownership | EXPECTED CHANGE |
| `cl/src/ui/bridge.py` | pywebview upload/submission boundary; routes ONLINE canonical upload vs LOCAL_OFFLINE compatibility | EXPECTED CHANGE |
| `cl/src/ui/encoder.py` | active baseline local-file -> base64 FileEncoder used by LOCAL_OFFLINE compatibility; ONLINE canonical path bypasses it | COMPATIBILITY / EXPECT NO CHANGE |
| `cl/src/core/agent_engine.py` | LOCAL_OFFLINE request-building owner that consumes `attached_files` through `process_attached_files`; preserved by first ONLINE canonical slice | LOCAL_OFFLINE COMPATIBILITY / EXPECT NO CHANGE |
| `cl/src/ui/web/js/components/console/normalizer.js` | preserve canonical asset identity | EXPECTED CHANGE |
| `cl/src/ui/web/js/components/console/blocks/fileBlock.js` | authenticated canonical content resolution for view/download | EXPECTED CHANGE |
| `cl/src/ui/web/js/components/console/blocks/imageBlock.js` | authenticated canonical image resolution | EXPECTED CHANGE |
| `cl/src/ui/web/js/components/console/blocks/mediaBlock.js` | authenticated canonical media resolution | EXPECTED CHANGE |
| `cl/src/schemas/attachment.py` | already-positive canonical shape | EXPECT NO CHANGE; CONDITIONAL ONLY |
| `cl/src/ui/web/js/components/console.js` | resolver wiring only if required by chosen UI composition | CONDITIONAL |
| server `/v1/assets` router/service | already-landed canonical server authority | NO CHANGE |
| provider runtime / F5 hydration | already-landed downstream execution | NO CHANGE |
| storage schema/migrations | outside first F6 slice | NO CHANGE |

Production CLAIM must stop and request fresh authority if implementation requires changes to:

- server CAS lifecycle/API semantics;
- storage schema/migration;
- provider runtime;
- session regeneration;
- deletion/reclamation/GC;
- CTX/R11/R12 ownership.

## 16. Explicitly CLOSED in CAS-F6

The following remain CLOSED unless separately released:

- provider-generated response/media ingestion -> CAS-F7;
- legacy `/v1/files` and legacy attachment cutover/backfill/removal -> CAS-F8;
- asset-bearing session regeneration release;
- provider remote cleanup/reclamation/delete;
- binding DELETING/DELETED production lifecycle;
- orphan provider-file cleanup;
- READY FileAsset deletion/release;
- FileBlob/ObjectStorage physical GC/reconciliation;
- automatic UNKNOWN/stale PROCESSING recovery;
- CTX Memory promotion/source-proof/retrieval ownership;
- R11 retention/destructive-GC ownership;
- R12 lease/recovery/checkpoint ownership;
- provider routing/fallback/deadline/model-selection ownership.

These are normative authority boundaries. This F6-0 evidence must not encode permanent live-source absence assertions that would prevent a later separately audited stage from opening one of them.

## 17. F6-0 exit requirements

F6-0 may be independently released for production CLAIM only when all are true:

- this contract is canonical and internally consistent;
- architecture evidence positively proves the existing server canonical asset surface and canonical attachment capability;
- architecture evidence does not freeze temporary implementation gaps as permanent source absence;
- the upload -> descriptor -> canonical attachment -> send state machine is frozen;
- partial-upload failure/retry behavior is frozen;
- authenticated preview/download resolution is frozen;
- legacy/F8 boundary is frozen;
- first production file matrix is frozen;
- Linux Architecture = GREEN;
- Windows Architecture = GREEN;
- independent exact-head audit = PASS;
- blocking CAS-F6 P0/P1 = NONE;
- production/runtime/schema/migration delta remains ZERO.

Before that gate:

```text
CAS-F6-0 = CONTRACT CANDIDATE
CAS-F6 production CLAIM = CLOSED
CAS-F7 = CLOSED
CAS-F8 = CLOSED
```

A successful F6-0 contract landing does not itself implement or authorize production F6 behavior.
