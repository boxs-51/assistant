# Skill V2 / DCS Implementation Roadmap

## Authority

- Tracker: #274
- C0 contract stage: #275
- P0 runtime foundation: #276
- Parent architecture: #156
- DCS-1: #159
- DCS-2: #160
- Policy: #85 v2.5
- Baseline: `main@e2395dabdac3c45e4b97cbc6a7ee51e08ab46d72`

This roadmap is coordination authority only. It grants no production CLAIM or merge authority.

---

## 1. Dependency graph

```text
#158 AOS-1 ------------------┐
                             ├──> #276 SKV2-P0
#275 SKV2-C0 docs contract --┘
                                      |
                                      v
                               #159 DCS-1
                                      |
                                      v
                               #160 DCS-2
                                      |
                        +-------------+-------------+
                        |                           |
                        v                           v
                  SKV2-R1 references          later Tool tracks
                        |
                        v
                  SKV2-C1 client sync
                        |
                        v
                  SKV2-X1 executable-Skill migration
```

C0 may progress before AOS-1 lands because it is docs-only. P0 must wait for both AOS-1 and C0 canonicalization.

---

## 2. Current source classification

### KEEP

- `se/src/application/policy/authorization.py`
  - canonical authorization authority.
- `se/src/runtimes/capability/catalog.py`
  - logical capability catalog.
- `se/src/runtimes/capability/contracts/definition.py`
  - current CapabilityKind and execution-mode compatibility.
- server `skill.load` lazy authorization concept.
- `agents/v1/*/instruction.md` guidance that available Skill descriptions are discovery metadata.
- current logical Tool IDs owned by Tools V1.

### MODIFY in SKV2-P0 after PRE-CLAIM

- `se/src/runtimes/capability/local_support_loader.py`
  - normalize/validate V2 Skill descriptors while preserving lazy instruction loading.
- `se/src/runtimes/agent/capabilities.py`
  - repair authorization gap and expose trusted Skill descriptors; do not implement DCS.
- `se/src/transport/gateway/api/v1/capability_router.py`
  - protect runtime-reserved Skill metadata/provenance.
- `se/src/domain/schemas/agent.py`
  - compatibility-safe clarification only if exact PRE-CLAIM still requires it.

### ADD in SKV2-P0

- `se/src/runtimes/capability/contracts/skill_manifest.py`
- `se/src/runtimes/agent/contracts/skills.py`
- `se/tests/architecture/test_skill_contract_v2.py`

### DEFER to #159 DCS-1

- `se/src/runtimes/agent/assembly.py`
- `se/src/runtimes/agent/contracts/context_assembly.py`
- `se/src/runtimes/agent/adapters/context.py`
- selected-tool assembly;
- zero-tool/zero-skill path;
- ActiveSkillSet integration;
- non-selected Tool execution fail-closed.

### DEFER to #160 DCS-2

- Skill-hint ranking;
- capability groups;
- progressive Tool expansion;
- selected schema/token estimation;
- uncertainty handling;
- bounded model-visible set.

### DEFER to SKV2-R1

- `skill.reference.load`;
- progressive reference bodies;
- reference-selection observability/limits.

### DEFER to SKV2-C1

- `cl/src/loader/skills_loader.py` canonical V2 normalization;
- `GatewayLLMClient.sync_registry()` metadata-only Skill registration;
- removal of eager `get_skill(..., load=True)` sync behavior;
- client UI representation changes if required.

### DEFER to SKV2-X1

- executable Skill deprecation/removal;
- `ExecutableSkillCapabilityDriver` migration;
- ONE_SHOT/LONG_RUNNING Skill compatibility cleanup.

---

## 3. SKV2-C0 — #275

Class: docs/contract only.

Exact candidate:

```text
ADD docs/skill_v2/SKILL_CONTRACT_V2.md
ADD docs/skill_v2/SKILL_DCS_IMPLEMENTATION_ROADMAP.md
MODIFY docs/ROADMAP_NAMESPACE_REGISTRY.md
```

Required freeze:
- Tool/Skill/helper boundary;
- shared Tool reuse;
- Manifest V2;
- capability_hints;
- activation;
- ActiveSkillSet;
- references;
- legacy executable-Skill boundary;
- document-format Skill layering;
- Tools V1/F7-T/CAS media ownership.

No `se/src/**`, `cl/src/**`, Tool implementation, schema or migration path is authorized.

---

## 4. SKV2-P0 — #276

State: RESERVED / NOT CLAIMED.

Recommended current maximum, subject to fresh exact-main PRE-CLAIM:

```text
ADD    se/src/runtimes/capability/contracts/skill_manifest.py
ADD    se/src/runtimes/agent/contracts/skills.py
MODIFY se/src/runtimes/capability/local_support_loader.py
MODIFY se/src/runtimes/agent/capabilities.py
MODIFY se/src/transport/gateway/api/v1/capability_router.py
MODIFY se/src/domain/schemas/agent.py        # only if still required
ADD    se/tests/architecture/test_skill_contract_v2.py
```

Do not treat this as released production authority.

Security/compatibility requirements:
- authorization on assigned Skill resolution;
- runtime-owned metadata cannot be spoofed;
- body/reference lazy loading;
- no implicit Tool grants;
- no silent `AgentDefinition.skills=[]` reinterpretation;
- no client/executable-Skill migration.

---

## 5. #159 DCS-1 handoff

DCS-1 starts only after:
- #158 canonical/healthy;
- #275 canonical;
- #276 canonical;
- fresh current-main/path audit.

DCS-1 owns:

```text
CapabilitySelectionContext
CapabilitySelectionResult
CapabilityWorkingSet
ActiveSkillSet integration
selected Tool assembly
zero-tool/zero-skill fast path
non-selected Tool fail-closed
```

Expected flow:

```text
Agent maximum Tool envelope
        +
trusted eligible Skill descriptors
        +
task/iteration context
        ↓
selector
        ↓
ActiveSkillSet
CapabilityWorkingSet
        ↓
context assembly
        ↓
InferenceRequest.tools = selected Tool subset
```

DCS-1 must not add durable recovery state unless separately released.

---

## 6. #160 DCS-2

DCS-2 owns optimization/progressive disclosure:

```text
Skill capability_hints
      ↓ ranking
Capability groups
      ↓
bounded initial WorkingSet
      ↓ tool result/task change
controlled expansion
```

Rules:
- groups and hints are metadata, never grants;
- no ALL-TOOLS fallback on uncertainty;
- expansion remains inside Agent/auth/routability envelope;
- selected tool-schema estimates are provider-neutral;
- live UBQ accounting remains UBQ-owned.

---

## 7. Document Tool and Skill roadmap

Skill V2 does not itself introduce document/image Tools.

Preferred logical Tool surface for a future Tools-owned program:

```text
document.read
document.inspect
document.create
document.edit
document.render
document.convert

image.generate
image.inspect
image.transform

chart.render
```

Preferred Skill surface:

```text
document-workflow
document-docx
document-pdf
document-xlsx
document-pptx
```

Relationship:

```text
document-workflow ─┐
document-docx ─────┼──> document.read / inspect / edit / render
document-pdf ──────┘

research-report ──────> document.* + web.* + chart.render
legal-memo ───────────> document.* + optional web.*
```

Do not clone a Tool per Skill.

Implementation helpers should move to shared domain/tool modules based on reuse:
- document-only helper -> document implementation package;
- cross-document helper -> shared document service;
- cross-domain MIME/filename/content helper -> common content service.

---

## 8. Media/render dependency

A future `document.render` that produces page PNG previews and a future `image.generate` must not create ad-hoc binary output semantics.

Current relevant authorities:
- #266 / TV1-T11: canonical Tools V1 producer contract direction for generated image/media;
- #74 / CAS-F7-T: post-COMMITTED generated-media canonicalization/persistence;
- #156/#161+: selection/target/routing;
- #166: future sandbox/provider-download -> CAS bridge, separately gated.

A future document/image Tool stage must run a fresh producer/transport/CAS audit and select the correct media class.

---

## 9. Test plan

### SKV2-P0
- legacy v1 manifest normalization;
- V2 activation/hints/reference descriptor validation;
- no body/reference reads during discovery;
- auth-denied assigned Skill absent;
- auth revoked before load -> denied;
- reserved metadata spoof denied;
- traversal/absolute/cross-Skill references denied;
- hint outside Tool envelope does not create authority;
- empty ActiveSkillSet valid.

### DCS-1
- no-tool simple request -> `tools=[]`;
- no relevant Skill -> `ActiveSkillSet=[]`;
- strict subset Tool visibility;
- unauthorized/non-selected Tool execution fails closed;
- selected Skill body only;
- deterministic per-iteration result.

### DCS-2
- many Skills sharing one Tool -> one logical Tool in WorkingSet;
- Skill hints affect ranking, not authority;
- controlled expansion;
- no ALL-TOOLS uncertainty fallback;
- selected schema/token estimate stable.

---

## 10. Integration order

```text
1. #275 docs contract
2. #158 AOS-1 integration (independent existing gate)
3. fresh #276 PRE-CLAIM
4. #276 implementation / CI / independent FINAL / integration
5. refresh #159 PRE-CLAIM against canonical #276
6. #159 implementation / integration
7. #160
8. SKV2-R1
9. SKV2-C1
10. SKV2-X1
```

#275 docs work may finish before #158; #276 still waits for #158 canonical health.

---

## 11. Stop conditions

STOP and re-audit if:
- a Skill stage needs to modify DCS selection files before #159 authority;
- a Skill package needs arbitrary filesystem/CAS resources;
- a Skill hint would create capability authority;
- a future document/image operation requires a new Tool ID without Tools-owned authority;
- generated media requires a transport/persistence semantic not already frozen;
- client/user Skill ownership becomes necessary;
- durable ActiveSkillSet/WorkingSet persistence is required;
- any exact path overlaps an active claimed PR.

---

## 12. Current next exact actions

1. Run independent docs/contract audit on #275 candidate.
2. Do not CLAIM #276 while #158 remains unmerged.
3. Complete #158 through its existing independent FINAL/integration path.
4. After #158 canonical + #275 canonical, refresh main and open independent #276 PRE-CLAIM.
5. Keep #159/#160 RESERVED until their updated upstream gates are satisfied.
