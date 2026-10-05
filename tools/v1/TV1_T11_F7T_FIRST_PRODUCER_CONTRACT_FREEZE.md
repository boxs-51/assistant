# TV1-T11-A — First F7-T Producer Contract Freeze

**Canonical workspace:** Issue #266  
**Policy:** Issue #85 v2.5  
**Class:** CONTRACT + ARCHITECTURE EVIDENCE ONLY  
**Production authority:** NONE  
**Production CLAIM:** NONE  
**Merge authority:** NONE  

## 0. Exact audit baseline

```text
audit baseline = main@945e267dfb00a729e070952bf1c88e703cac78ac
baseline source = CTX-F5-3I-B8 PR #264 / zero-production docs+evidence
baseline Architecture #2122 / 37265971728 = GREEN/GREEN

prior CAS-F7-T-A0 canonical main = 3b53969fbdfb68a47fd2a4a0262d02fc51ea02b6
CAS-F7-T-A0 post-merge Architecture #2120 / 37264857889 = GREEN/GREEN

main drift from 3b53969f -> 945e267d = NON_MATERIAL to TV1-T11-A
production/runtime/schema/migration overlap = ZERO
```

TV1-T11-A is a fresh successor to completed Tools V1 / TV1-T10. It does not reopen
Issue #38 and does not inherit production authority from a closed stage.

The exact candidate identity frozen by this document is:

```text
capability_id = desktop.screenshot
capability_version = 1.0
physical package = desktop_automation
future physical package version = 2.1.0
source contract = F7T_INLINE_BASE64_V1
media kind = image
MIME = image/png
placement = CLIENT only
semantic target = CLIENT_LOCAL
fallback = NONE
```

No concrete CAS enrollment entry is authorized by this document.

---

## 1. Ownership boundary

### Tools V1 successor owns

TV1-T11 owns only the producer-side contract:

- canonical logical ID and logical version;
- physical desktop producer behavior;
- canonical Tools V1 ToolResult shape;
- implementation-location eligibility for this export;
- producer-local privacy, bounds and failure behavior;
- client placement evidence.

### CAS #74 owns

CAS-F7-T remains the sole owner of:

- post-COMMITTED F7-T admission;
- durable capability/version enrollment;
- source-result lineage validation;
- canonical asset ingestion;
- asset:// publication and idempotency.

TV1-T11 must not import CAS or call AssetService.

### Issue #156 owns

Issue #156 remains the owner of future semantic invocation target and target-aware
routing. The landed/current #156 contract freezes:

```text
CLIENT_LOCAL MUST NOT silently fall back to SERVER/SANDBOX.
```

TV1-T11-A adopts that boundary as dependency evidence but receives no #156
production authority.

### PTC/provider owns

Provider/PTC is not part of the first producer. No provider image API, provider
alias, provider file ID, URL, path, sandbox object or remote handle participates.

---

## 2. Current-source audit findings

### P1-TV1-T11-A-PRIVACY-HITL-1 — screenshot is not LOW/MEDIUM read-only

A screenshot reads potentially sensitive visible desktop content. Current
LocalCapabilityExecutor evaluates Metadata V2 base_risk and invokes HITLManager
before the callable. HITLManager auto-allows LOW/MEDIUM and requires explicit
approval for HIGH/CRITICAL.

Freeze:

```text
desktop.screenshot.base_risk = HIGH
desktop.screenshot.effects = [READ, PRIVILEGED]
approval = PER INVOCATION
missing approval callback = DENY / FAIL CLOSED
```

No screenshot call may be classified LOW or MEDIUM merely because it is
side-effect-free.

### P1-TV1-T11-A-PLACEMENT-2 — current server loader would create an unsafe fallback

Current source facts:

- client V2 placement is exact-ID opt-in through
  `tools_config.enabled_v2_capabilities`;
- server Local Tool loader currently registers every canonical V2 export it
  discovers;
- current CapabilityRoutingPolicy prefers the same-connection CLIENT, but keeps
  SERVER/non-client implementations as valid fallback candidates;
- #156 freezes CLIENT_LOCAL as no-silent-fallback.

Therefore adding only a `desktop.screenshot` export to `desktop_tool.py`
would be unsafe. It could make server/headless desktop capture a routable
fallback after client loss.

Freeze:

```text
desktop.screenshot implementation locations = [CLIENT]
SERVER implementation = MUST NOT EXIST
MCP implementation = MUST NOT EXIST
DECLARATIVE implementation = MUST NOT EXIST
foreign CLIENT implementation = MUST NOT ROUTE
same CLIENT connection = required
fallback_policy = NONE
```

TV1-T11-B must add an explicit canonical Metadata V2 implementation-location
fence rather than relying on routing order.

The producer-side metadata term frozen for B is:

```text
execution_locations = ["CLIENT"]
```

Rules:

1. `execution_locations` is implementation availability, not semantic resource
   identity.
2. It does not replace #156 `CapabilityInvocationTarget.resource_scope`.
3. Allowed values for this Tools V1 field are exactly `SERVER` and `CLIENT`.
4. The field is optional for backward compatibility.
5. Absence means the historical current behavior:
   `["SERVER", "CLIENT"]`.
6. Server loader must skip an export when SERVER is absent.
7. Client loader must skip an export when CLIENT is absent even if its ID is
   mistakenly listed in the client allowlist.
8. Location metadata must not enter canonical logical-definition equality or
   logical version identity; it controls implementation admission only.

If #156/#161 lands a conflicting canonical placement contract before B CLAIM,
that is MATERIAL drift and B must stop for a fresh overlap audit.

### P1-TV1-T11-A-PERMISSION-3 — do not invent a non-enforced screen permission

Current Metadata V2 carries `required_permissions`, but the current
LocalCapabilityExecutor does not use that list as an OS permission enforcement
authority. The screenshot backend/OS may independently deny capture.

Freeze for first producer:

```text
required_scopes = []
required_permissions = []
danger_patterns = []
```

TV1-T11 must not write `required_permissions=["screen_capture"]` and imply
enforcement that does not exist.

OS/backend capture denial, missing backend support or permission failure must
return a canonical failed ToolResult and must never fall back to SERVER.

### P1-TV1-T11-A-INLINE-SIZE-4 — inline media needs a producer-local transport cap

F7-T source bytes are carried inline as base64 through client realtime transport
and then durable AgentToolResult output. Current repository startup does not
declare an explicit application-owned WebSocket message-size contract.

Therefore CAS `max_upload_bytes` is not an acceptable producer limit.

Freeze:

```text
MAX_SCREENSHOT_WIDTH = 7680
MAX_SCREENSHOT_HEIGHT = 4320
MAX_SCREENSHOT_PIXELS = 33177600
MAX_SCREENSHOT_PNG_BYTES = 8388608
max F7-T items = exactly 1
```

The 8 MiB limit applies to decoded PNG bytes before base64. It is intentionally
stricter than CAS admission.

TV1-T11-B must include a real TCP/WebSocket near-bound regression proving that
an admitted result traverses CLIENT -> realtime -> durable COMMITTED tool
result without truncation. If 8 MiB cannot be proven on the canonical transport,
B must LOWER the producer bound and rerun audit. B may not enlarge transport
authority or server WebSocket settings inside the frozen production scope.

### P1-TV1-T11-A-MODEL-CONTEXT-5 — raw F7-T base64 must not enter model inference

Current Agent context assembly appends every successful ToolExecutionResult to the
next model request with:

```python
InferenceMessage(
    role="tool",
    name=result.capability_id,
    tool_call_id=result.tool_call_id,
    content=jsonable(result.output),
)
```

For an inline screenshot producer this would copy the full base64 image into the
next inference request. That is not a transport-only concern: it creates
unbounded/expensive model-context pressure and may expose screen pixels to a
provider that only needs to know that capture succeeded.

The first producer therefore freezes a separate model-facing projection.

Durable authority remains unchanged:

```text
AgentToolResultRecord.output = exact successful F7T_INLINE_BASE64_V1 ToolResult
CTX TOOL_RESPONSE_PAYLOAD source = exact durable committed output
CAS F7-T source = exact durable committed output
```

Only the in-memory model-facing InferenceMessage content is projected.

For exact:

```text
result.success = true
result.capability_id = desktop.screenshot
result.output = strict successful Tools V1 F7T_INLINE_BASE64_V1 envelope
```

the next inference sees exactly this bounded projection:

```json
{
  "$f7t_media_projection": {
    "source_contract": "F7T_INLINE_BASE64_V1",
    "binary_omitted": true,
    "items": [
      {
        "ordinal": 0,
        "media_kind": "image",
        "mime_type": "image/png",
        "filename": "desktop-screenshot.png",
        "size_bytes": 123,
        "sha256": "<64 lowercase hex>"
      }
    ]
  }
}
```

Projection rules:

1. the durable ToolResult is never modified;
2. projection runs only for the exact `desktop.screenshot` capability and an
   exact strict successful F7-T envelope;
3. no `data_base64`, PNG bytes, alternate URI/path/provider handle or hidden
   image content enters model-facing message content;
4. projection copies only bounded non-secret metadata already present in the
   committed result;
5. malformed/near-match output is not silently normalized into the projection;
6. projection is deterministic across ordinary, resume and recovery context
   rebuilding;
7. projection does not create an `asset://` identity and does not claim CAS
   publication succeeded;
8. projection is not a CTX source mutation, Memory promotion, retrieval result or
   ContextBuilder persistence authority;
9. the model-facing projection may be persisted only where the existing Agent
   transcript/inference-request representation already persists model-facing
   messages; it must never replace the durable ToolResult source row;
10. F7-T rejection/failure after COMMITTED result never changes the projection
    into a tool failure and never authorizes recapture.

This closes model-context payload pressure at design level but adds one
cross-track production path to the future B maximum:
`se/src/runtimes/agent/adapters/context.py`.

That path is not owned by TV1-T11 by adjacency. Before B production CLAIM, a
fresh independent overlap/PRE-CLAIM audit must identify and obtain release from
the then-current canonical Agent context/execution owner/workspace and re-check
Issue #156 because its roadmap also names this adapter for future selection
state. No release is implied by this A contract.

### P1-TV1-T11-A-SCREEN-SCOPE-6 — capture scope must be deterministic

First producer supports exactly the primary display.

```text
public input properties = {}
public required inputs = []
bind = {"action": "screenshot"}
capture target = current local primary display
region/window/application selection = CLOSED
multi-monitor aggregation = CLOSED
cursor injection = CLOSED
OCR = CLOSED
```

The future implementation must compare the image dimensions returned by the
capture backend with the current primary-display size reported by the same
desktop backend. A mismatch is failure, not authority to crop, stitch, rescale
or choose another display.

### P1-TV1-T11-A-CAPTURE-BACKEND-7 — screenshot backend dependency must be explicit

The selected first implementation shape relies on `pyautogui.screenshot()` /
PyScreeze returning an image object that can be encoded to PNG in memory.

Current repository dependency facts are:

```text
PyAutoGUI==0.9.54
PyScreeze==1.0.1
Pillow pin = ABSENT
```

The PyAutoGUI screenshot contract requires Pillow for screenshot image support.
A production design that calls the screenshot backend while leaving Pillow
undeclared would depend on an ambient/transitive environment accident and would
not be reproducible.

Freeze for B:

```text
requirements owner path = requirements.txt
Pillow dependency = EXPLICIT EXACT PIN REQUIRED
unbounded / unpinned Pillow = FORBIDDEN
new requirements file = FORBIDDEN
server-side screenshot implementation = STILL FORBIDDEN
```

Adding the library to the shared root lockfile grants dependency availability
only. It does not create a SERVER implementation, server capture authority or
routing fallback.

TV1-T11-B PRE-CLAIM must select one exact Python-3.12-compatible Pillow version
and independently verify:
- clean installation on the repository Linux + Windows CI environments;
- the chosen PyAutoGUI/PyScreeze path can produce the in-memory image object
  needed by the frozen encoder on an eligible client environment;
- production screenshot code never saves through a filesystem filename;
- no second image/capture dependency is introduced.

If the chosen backend cannot satisfy the frozen primary-display, in-memory PNG
and near-bound transport proof within the resulting path ceiling, B must stop
and reopen PRE-CLAIM instead of adding another dependency or fallback backend.

### P1-TV1-T11-A-REPLAY-SAFETY-8 — time-varying capture is not replay-safe

Screenshot is read-only with respect to external mutation, but repeated execution
is not semantically equivalent: the visible screen may change between attempts.

Current R6/R7 authority treats `IDEMPOTENT` work with an ambiguous remote
outcome as replay-safe. That is too permissive for a sensitive capture.

Freeze:

```text
desktop.screenshot.idempotency = UNKNOWN
OUTCOME_UNKNOWN automatic replay = FORBIDDEN
reconciliation UNKNOWN / NOT_FOUND automatic replay = FORBIDDEN
fresh recapture after ambiguous dispatch = REQUIRES NEW EXPLICIT AUTHORITY / NOT THIS STAGE
```

The HIGH-risk approval gate does not convert an ambiguous previous capture into
replay-safe work. Existing reconciliation must first recover a terminal result;
if it cannot prove the prior outcome, the invocation remains fail-closed under
the existing UNKNOWN-idempotency semantics.

No producer-local retry loop is permitted.

---

## 3. Exact logical export contract

Future canonical Metadata V2 export:

```python
{
    "id": "desktop.screenshot",
    "version": "1.0",
    "name": "desktop.screenshot",
    "description": (
        "Capture the current local primary display as one bounded PNG. "
        "The image may contain sensitive visible information and requires "
        "per-invocation local approval."
    ),
    "bind": {"action": "screenshot"},
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
        "required": [],
    },
    "output_schema": "<canonical ToolResult around exact F7-T data schema>",
    "kind": "TOOL",
    "execution_mode": "ONE_SHOT",
    "idempotency": "UNKNOWN",
    "effects": ["READ", "PRIVILEGED"],
    "base_risk": "HIGH",
    "required_scopes": [],
    "required_permissions": [],
    "danger_patterns": [],
    "execution_locations": ["CLIENT"],
}
```

Existing desktop export logical versions and semantics remain unchanged.

The physical package version must advance from `2.0.0` to `2.1.0` because
the package gains a new externally visible action/export. The logical producer
version remains `1.0`.

---

## 4. Exact successful ToolResult

The only successful data shape is:

```json
{
  "$f7t_media": {
    "contract": "F7T_INLINE_BASE64_V1",
    "items": [
      {
        "ordinal": 0,
        "media_kind": "image",
        "mime_type": "image/png",
        "filename": "desktop-screenshot.png",
        "encoding": "base64",
        "size_bytes": 123,
        "sha256": "<64 lowercase hex>",
        "data_base64": "<canonical RFC4648 base64 without whitespace>"
      }
    ]
  }
}
```

The outer canonical ToolResult must be:

```text
ok = true
tool = desktop_automation
action = screenshot
data keys = exactly {"$f7t_media"}
error = null
meta.version = 2.1.0
meta.truncated = false
meta.warnings = []
```

No duplicate convenience fields are allowed outside `$f7t_media`.

The producer computes:

```text
png_bytes = exact in-memory PNG emitted by screenshot backend image.save(...)
size_bytes = len(png_bytes)
sha256 = lowercase sha256(png_bytes).hexdigest()
data_base64 = base64.b64encode(png_bytes).decode("ascii")
```

The producer must reject before constructing a success result when:

- width/height are invalid;
- width exceeds 7680;
- height exceeds 4320;
- pixel count exceeds 33177600;
- encoded PNG is empty;
- PNG bytes exceed 8388608;
- image dimensions disagree with current primary-display size;
- backend capture/encoding fails;
- cancellation is observed before the target call by existing CLIENT execution
  machinery;
- local approval is denied.

No raw image bytes may be written to disk as an intermediate artifact.

---

## 5. Privacy and logging boundary

Screenshot contents are sensitive payload.

The implementation must not:

- log `data_base64`;
- log PNG bytes;
- log hashes together with image content;
- add window titles, process names, clipboard content, OCR text or inferred user
  activity to the ToolResult;
- save screenshots to repository/temp/user paths;
- reuse the screenshot as a hidden UI telemetry surface;
- auto-capture on registration, heartbeat, retry, resume or reconnect;
- capture before the local HIGH-risk approval decision.

The only durable image-bearing object before CAS activation is the committed
ToolResult itself, as required by F7-T.

---

## 6. Retry/reconnect and durable-result boundary

`desktop.screenshot` is read-only but intentionally declares idempotency
`UNKNOWN`: one execution attempt may observe a different screen than another.

Therefore:

- invocation/reconciliation identity remains owned by existing R6/R7 machinery;
- producer code must not create a new retry loop;
- producer code must not re-run capture merely because F7-T later rejects or
  fails;
- CAS activation observes only durable COMMITTED successful result output;
- reconnect replay of an already terminal client outcome must reuse the durable
  terminal result rather than silently recapture;
- F7-T failure never authorizes another screenshot.

---

## 7. CLIENT placement and #156 compatibility

The future producer represents:

```text
resource_scope = CLIENT_LOCAL
stable client affinity = required
connection affinity = current connection
fallback_policy = NONE
```

TV1-T11-B does not implement `CapabilityInvocationTarget`; #161 remains the
future owner of that semantic target object.

Until #161 is canonical, absence of a SERVER implementation is the mandatory
fail-closed fence that prevents current CapabilityRoutingPolicy from silently
falling back to a server desktop.

No #156 child is opened or claimed by this contract.

---

## 8. UBQ boundary

Canonical per-tool quota identity is:

```text
capability_id = desktop.screenshot
```

TV1-T11 does not create a new quota model, refund, recharge or retry budget.

One logical screenshot invocation consumes the ordinary UBQ logical tool-call
accounting applicable to the canonical capability ID when that UBQ path is
active.

The screenshot byte count is not independently converted into UBQ compute/cost
authority by this stage.

---

## 9. Exact TV1-T11-A two-file scope

TV1-T11-A may change only:

1. `tools/v1/TV1_T11_F7T_FIRST_PRODUCER_CONTRACT_FREEZE.md`
2. `tools/v1/test/test_t11_a_f7t_first_producer_contract.py`

Both are NEW.

TV1-T11-A must not change:

```text
tools/v1/desktop_tool.py
tools/v1/_shared/metadata.py
cl/**
se/**
agents/**
requirements*
schema/migrations
provider code
CAS production code
#156 production code
```

Production/runtime/config/schema/migration delta is exactly ZERO.

---

## 10. Exact TV1-T11-B maximum — frozen but NOT released

This section identifies the smallest currently justified production maximum.
It is not a PRE-CLAIM PASS and grants no production authority.

Maximum twelve paths:

1. `requirements.txt`
   - add one exact Pillow pin required by PyAutoGUI/PyScreeze screenshot support;
   - no new requirements file and no unrelated dependency churn.

2. `tools/v1/_shared/metadata.py`
   - validate optional per-export `execution_locations`;
   - allowed set exactly SERVER/CLIENT;
   - absent field preserves historical SERVER+CLIENT behavior;
   - location eligibility is excluded from logical-definition identity.

3. `tools/v1/desktop_tool.py`
   - add screenshot action/export;
   - physical version 2.1.0;
   - primary-display capture;
   - bounds, PNG memory encode, hash/base64 envelope;
   - no disk write and no CAS/provider/#156 import.

4. `se/src/runtimes/capability/local_tool_loader.py`
   - do not create SERVER implementation for exports whose
     `execution_locations` excludes SERVER;
   - no CapabilityRoutingPolicy change.

5. `cl/src/loader/local_tools.py`
   - do not create/advertise CLIENT implementation for exports whose
     `execution_locations` excludes CLIENT;
   - preserve exact-ID opt-in.

6. `se/src/runtimes/agent/adapters/context.py`
   - preserve exact durable ToolExecutionResult/AgentToolResult output;
   - for exact successful `desktop.screenshot` F7-T envelope only, replace
     model-facing message content with the frozen
     `$f7t_media_projection` metadata-only shape;
   - never pass `data_base64` into next inference message;
   - no AgentRuntime lifecycle, retry, persistence-row or CTX source mutation.

7. `cl/config/setting.json`
   - add exactly `desktop.screenshot` to `enabled_v2_capabilities`.

8. `cl/tests/test_t9_c_client_placement.py`
   - supersede the historical exact-24 assertion only for the additive T11
     screenshot capability;
   - preserve all original 24 T9 IDs and no-Web invariant.

9. `tools/v1/test/test_t9_c_metadata.py`
   - preserve all eight historical desktop logical exports unchanged;
   - append exactly `desktop.screenshot`;
   - assert its 1.0 logical version, immutable screenshot bind, strict empty
     public input schema, HIGH risk, READ/PRIVILEGED effects and CLIENT-only
     implementation metadata.

10. `cl/tests/test_t9_f_execution_equivalence.py`
   - preserve execution equivalence for historical T9 desktop operations;
   - update only the expected physical desktop package provenance from 2.0.0 to
     2.1.0;
   - no logical-version or action-semantic rewrite.

11. `se/tests/architecture/test_t9_d_real_server_client_parity.py`
   - preserve the historical 24 T9 SERVER+CLIENT parity assertions;
   - permit exactly one additive T11 `desktop.screenshot` CLIENT definition;
   - prove `desktop.screenshot` has CLIENT implementation only and no SERVER
     counterpart while the original T9 definitions remain byte/contract
     equivalent.

12. `se/tests/architecture/test_tv1_t11_b_desktop_screenshot_producer.py`
   - focused cross-layer production/evidence gate covering:
     metadata validation; logical/physical versions; UNKNOWN idempotency and
     OUTCOME_UNKNOWN no-replay proof; CLIENT-only registration;
     SERVER absence; HIGH-risk metadata; local HITL before target call; exact
     ToolResult/F7-T envelope; dimensions/byte bound/hash/base64; no disk
     intermediate; current routing has no server fallback candidate;
     inference projection contains no base64 and leaves the durable result
     byte-for-byte/JSON-equivalent unchanged; ordinary/resume/recovery
     projection determinism; near-bound real TCP/WebSocket committed-result
     transport evidence.

Any required thirteenth path invalidates this maximum and requires a fresh
TV1-T11-B PRE-CLAIM audit before code changes.

### 10.1 Mandatory cross-track release before B CLAIM

The twelve-path maximum is a scope ceiling, not production authority.

B PRE-CLAIM remains HOLD until a fresh audit explicitly releases the exact
`se/src/runtimes/agent/adapters/context.py` model-projection edit from the
then-current canonical Agent context/execution owner/workspace and confirms no
conflict with then-current #156 selection/target work.

The release must freeze:

- durable ToolResult output remains immutable;
- only model-facing content is projected;
- projection is exact-capability + exact-envelope gated;
- no generic ToolResult redaction framework is opened;
- no CTX Memory/promotion/retrieval authority transfers;
- no Agent retry/replay/continuation lifecycle authority transfers;
- no CAS enrollment/activation authority transfers.

Until that release exists:

```text
TV1-T11-B PRE-CLAIM = HOLD
TV1-T11-B production CLAIM = NONE
```

Explicitly outside B:

```text
CapabilityRoutingPolicy changes
CapabilityInvocationTarget implementation
AgentRuntime lifecycle/loop changes
Agent persistence schema/model changes
CAS enrollment/activation
CAS A1
provider/PTC
sandbox
schema/migrations
additional image/capture dependencies beyond the one frozen Pillow pin
new requirements files
UI redesign
multi-monitor/window/region capture
video capture
OCR
generic tool-result redaction
```

If #156/#161 becomes canonical before B CLAIM and materially changes location,
target or context-projection semantics, B must refresh rather than preserving
this twelve-path maximum by force.

---

## 11. TV1-T11-A exit gate

A may reach independent contract FINAL only when:

1. exact two-file A scope is preserved;
2. production delta remains zero;
3. exact candidate HEAD has fresh Architecture Linux + Windows GREEN;
4. privacy/HITL contract is present;
5. CLIENT-only/no-SERVER-fallback contract is present;
6. inline size and real-network proof requirement is present;
7. model-facing raw-base64 exclusion and deterministic projection contract is present;
8. existing desktop exports remain untouched;
9. no unresolved contract-design P0/P1 remains;
10. reviews/threads are clean;
11. current-main drift is classified under Policy #85.

A FINAL PASS may release only a separate TV1-T11-B PRE-CLAIM audit.

It must not directly authorize B production.

---

## 12. CAS handoff gate

Issue #74 remains:

```text
P1-CAS-F7T-ACT-PRODUCER-1 = OPEN / GAP-HOLD
concrete enrollment = EMPTY
A1 PRE-CLAIM = HOLD / NOT CLAIMABLE
A1 production CLAIM = NONE
```

until all of the following occur:

1. a later TV1-T11 production stage receives its own PRE-CLAIM and CLAIM;
2. `desktop.screenshot@1.0` is implemented;
3. producer exact-head CI is GREEN;
4. independent producer FINAL passes;
5. producer lands;
6. post-merge Linux + Windows Architecture is GREEN;
7. Issue #266 records an explicit producer-owner handoff naming exact
   `desktop.screenshot@1.0`.

Only then may #74 run a fresh A1 PRE-CLAIM against the landed A0 six-file
maximum.

No A1 production CLAIM is implied or authorized here.
