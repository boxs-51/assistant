# Skill Contract V2

## Status and authority

- Tracker: Issue #274
- Contract stage: Issue #275 / SKV2-C0
- Parent architecture: Issue #156
- Policy: Issue #85 v2.5
- Audit baseline: `main@e2395dabdac3c45e4b97cbc6a7ee51e08ab46d72`
- Class: CONTRACT / DOCUMENTATION ONLY
- Production authority: NONE
- Production CLAIM: NONE
- Merge authority: NONE

This document freezes Skill V2 semantics before Dynamic Capability Selection.

---

## 1. Canonical semantic

A Skill is procedural knowledge for an Agent.

A Skill may tell the model:
- when a procedure applies;
- which steps to follow;
- which logical capabilities are useful;
- how to validate the result;
- which bounded reference to consult.

A Skill MUST NOT:
- execute a Tool by itself;
- grant a Tool;
- widen an Agent capability envelope;
- bypass AuthorizationService;
- choose physical routing;
- acquire sandbox, CAS, UBQ, CTX Memory or recovery authority;
- hide an additional inference/runtime behind a context-only Skill.

Canonical separation:

```text
AGENT
  reasoning + task execution runtime

SKILL
  procedural knowledge / playbook

TOOL / LOGICAL CAPABILITY
  model-selectable executable operation

SHARED HELPER / SERVICE
  implementation code below Tool boundary

DCS
  per-iteration model-visible capability selection
```

Normative invariant:

> Skills reference shared logical capabilities; they do not own, clone, wrap, rename, grant or execute those capabilities.

---

## 2. Tool versus helper boundary

A function belongs in CapabilityCatalog only when the model may independently select it as an operation or when it needs an explicit execution/authorization boundary.

Examples of logical Tools:

```text
document.read
document.inspect
document.edit
document.create
document.render
document.convert

image.generate
image.inspect
image.transform

chart.render

web.search
file.read
terminal.run
```

Examples of ordinary helpers/services:

```text
DocxParser
PdfPageRenderer
StyleAnalyzer
RelationshipValidator
MimeDetector
FilenameSanitizer
TableNormalizer
```

Helpers:
- have no capability_id merely because a Skill/Tool uses them;
- are not exposed to the model;
- are not DCS candidates;
- may be shared by multiple Tools.

Side-effecting/model-directed operations SHOULD cross a logical capability boundary rather than hide behind a helper.

---

## 3. Shared Tool reuse

One logical Tool may support many Skills.

Example:

```text
document.read
      ▲
      ├── document-workflow
      ├── document-docx
      ├── document-pdf
      ├── research-report
      └── legal-memo
```

Do not create per-Skill copies such as:

```text
document-workflow.read
legal-memo.read
research-report.read
```

when the executable operation is semantically the same.

---

## 4. Canonical package

Preferred server representation:

```text
skills/v2/<skill-id>/
  manifest.json
  instruction.md
  references/
    ...
```

Responsibilities:
- `manifest.json`: machine-readable metadata and activation contract;
- `instruction.md`: small procedural guide;
- `references/*`: progressively disclosed supporting knowledge.

Client `SKILL.md` may remain a source/UX format during migration, but it MUST normalize to the same canonical model and MUST NOT become a second runtime authority.

---

## 5. Manifest V2

Conceptual schema:

```json
{
  "schema_version": "2",
  "skill_id": "document-docx",
  "version": "1.0.0",
  "name": "DOCX Workflow",
  "description": "Procedural guidance for DOCX work",

  "instruction": {
    "path": "instruction.md"
  },

  "activation": {
    "mode": "AUTO_ELIGIBLE",
    "intents": ["document", "docx"],
    "keywords": ["word", "docx"]
  },

  "capability_hints": [
    {
      "capability_id": "document.read",
      "purpose": "Read document structure/content",
      "stage": "inspect",
      "required": false
    },
    {
      "capability_id": "document.inspect",
      "purpose": "Inspect formatting and structure",
      "stage": "inspect",
      "required": false
    }
  ],

  "references": [
    {
      "reference_id": "styles",
      "path": "references/styles.md",
      "description": "DOCX style guidance",
      "activation_tags": ["style", "formatting"]
    }
  ]
}
```

Unknown security-sensitive/reserved fields must fail validation rather than silently changing runtime authority.

---

## 6. capability_hints

`capability_hints` are advisory only.

They MAY:
- influence DCS candidate ranking;
- document the stage/purpose where a Tool is normally useful;
- help determine partial applicability.

They MUST NOT:
- register a Tool;
- add a Tool to AgentDefinition.tools;
- directly mutate CapabilityWorkingSet;
- bypass auth/routability;
- choose a physical implementation.

Canonical intersection:

```text
(task-derived candidates ∪ advisory Skill hints)
∩ Agent maximum Tool envelope
∩ Authorization
∩ routability/availability
∩ DCS selection policy
= CapabilityWorkingSet
```

`required=true` means procedurally important, not authorization-granting.

---

## 7. Activation

Activation modes:

```text
ON_DEMAND
AUTO_ELIGIBLE
ALWAYS_ON
```

- ON_DEMAND: default; descriptor may be visible, body loads only after selection.
- AUTO_ELIGIBLE: selector may activate when task match is strong.
- ALWAYS_ON: exceptional; reserved for very small universal reviewed instructions.

Selection is not authorization.

Authorization must be checked at discovery/eligibility and again at activation/load when current identity matters.

---

## 8. ActiveSkillSet

`ActiveSkillSet` is an execution/iteration materialization of selected Skill knowledge.

Conceptual shape:

```text
ActiveSkillSet
  execution_id
  iteration
  revision
  skills[]
```

Each ActiveSkill records:
- skill_id;
- version;
- activation source/reason;
- instruction;
- loaded reference IDs;
- capability hints;
- partial applicability/unavailable hinted capabilities.

Invariant:

```text
ActiveSkillSet ⊆ authorized/eligible Skill envelope
```

Zero active Skills is valid.

ActiveSkillSet and CapabilityWorkingSet are separate objects.

Skill activation may contribute advisory candidates/ranking signals but cannot mutate Tool visibility directly. DCS must also support task-derived Tool selection when ActiveSkillSet is empty.

No durable ActiveSkillSet persistence is opened by this contract.

---

## 9. References

References are bounded Skill-private knowledge resources.

They are not arbitrary filesystem authority.

A future reference loader must verify:
- the Skill is active/eligible as required;
- exact skill/version identity;
- reference belongs to that Skill package;
- relative contained path;
- no absolute/traversal/symlink escape;
- size bounds;
- current authorization.

Conceptual model-facing identity:

```text
skill://document-docx/reference/styles
```

Physical host paths remain implementation detail.

`skill.reference.load` is deferred to SKV2-R1; it is not required for the initial P0 foundation.

---

## 10. Current repository migration

Current useful behavior to preserve:
- server Skill manifest discovery is lazy;
- `skill.load` is restricted to Agent execution;
- `skill.load` rechecks authorization;
- available Skill summaries can be exposed without body reads;
- client SkillManager initially discovers only frontmatter.

Current migration gaps:
- assigned Skill resolution lacks an authorization check before instruction injection;
- assigned Skills are eagerly injected by DefaultAgentContextAssembler;
- HTTP Skill registration can carry caller metadata that must not spoof runtime-owned provenance;
- client sync eagerly materializes all Skill bodies;
- executable Skill compatibility coexists with context-only semantics;
- no ActiveSkillSet exists.

---

## 11. Executable Skill compatibility

Canonical V2 Skill semantics are CONTEXT_ONLY.

Legacy:
- ONE_SHOT;
- LONG_RUNNING;
- ExecutableSkillCapabilityDriver;

remain compatibility surfaces until a separately gated migration.

Target classification:

```text
procedural reasoning knowledge -> Skill
reasoning/execution runtime -> Agent
model-selectable operation -> Tool
deterministic composition -> Workflow/Macro if introduced
ordinary reusable implementation -> helper/service
```

SKV2-C0 does not delete compatibility surfaces.

---

## 12. AgentDefinition.skills compatibility

Current `AgentDefinition.skills` is an assigned/preloaded Skill list.

Do not silently reinterpret:

```text
skills=[]
```

as either:
- no Skill allowed; or
- all authorized Skills allowed.

A later migration may introduce an explicit Skill envelope/policy with unambiguous modes.

Until then V2 selection must preserve compatibility and fail closed where interpretation is ambiguous.

---

## 13. Document-format Skill layering

Preferred Skill hierarchy:

```text
document-workflow
  ├── document-docx
  ├── document-pdf
  ├── document-xlsx
  └── document-pptx
```

`document-workflow` owns generic procedure:
- inspect input;
- determine format;
- preserve requested content;
- make requested changes;
- validate output;
- render/inspect final result when needed.

Format Skills own procedural differences:
- DOCX: named styles, headings, sections, tables, headers/footers, relationships;
- PDF: pages, geometry, text layer, fonts, annotations, scanned/raster content;
- XLSX: sheets, formulas, cell formats, merged cells, named ranges;
- PPTX: slides, layouts, masters, placeholders, speaker notes, positioning.

Avoid tiny Skill explosion such as one Skill per DOCX table/heading/header concern. Put those topics in references.

---

## 14. Document/image Tool ownership

Skill does not own rendering/generation implementations.

Recommended logical capabilities:

```text
document.render
  render document/page visual previews for layout/QA

image.generate
  generate a new independent image

image.inspect
  analyze an image

image.transform
  crop/resize/convert/transform an image

chart.render
  data -> chart
```

A DOCX/PDF rendering implementation may use format-specific render helpers internally without creating separate model-visible helper Tools.

Generated media must consume canonical Tools V1 / F7-T / CAS boundaries where applicable. This contract does not create a binary transport, CAS identity or persistence mechanism.

---

## 15. Cross-track ownership

- #156: parent Agent-only/capability architecture.
- #158: AOS-1 Agent resolution foundation.
- #159: DCS-1 CapabilitySelectionContext/CapabilityWorkingSet and ActiveSkillSet integration.
- #160: DCS-2 grouping, Skill-hint ranking, lazy expansion and estimates.
- #15: CTX Memory/ContextSnapshot/CompactContext authority.
- #74: durable generated-media/CAS authority.
- #266: Tools V1-T11 first F7-T producer contract.
- UBQ #141-#149: renewable resource accounting and timeout migration.

No cross-track authority transfers by reference.

---

## 16. Final invariant

```text
SKILL TELLS THE MODEL HOW TO WORK.
DCS DECIDES WHAT THE MODEL SEES.
AUTHORIZATION DECIDES WHAT IT MAY USE.
TOOL/CAPABILITY RUNTIME DECIDES HOW IT EXECUTES.
HELPERS IMPLEMENT OPERATIONS WITHOUT BECOMING MODEL-VISIBLE AUTHORITY.
```
