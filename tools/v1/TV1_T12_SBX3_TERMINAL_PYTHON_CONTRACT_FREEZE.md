# TV1-T12-A — SBX-3 Terminal and Python Ownership Contract Freeze

**Canonical workspace:** Issue #371 (TV1-T12), Tools V1 successor to completed #38
**Policy:** Issue #85 v2.5 plus stricter #371 / #156 / #164 / #147 fences
**Class:** CONTRACT + ARCHITECTURE EVIDENCE ONLY
**Released independent PRE-CLAIM:** #371 comment 6055282311
**Owner CLAIM:** #371 comment 6060382557
**Development baseline:** main@7332af469c074fb4331eee841f0ee121c7738f3e
**Baseline Architecture:** #2376 / run 37778284808, Linux GREEN + Windows GREEN
**Parent:** none; zero-production contract at stack depth 0
**Production authority:** NONE
**Production CLAIM:** NONE
**Merge authority:** NONE

This freeze is a design/ownership gate, not an executable sandbox and not
permission for terminal, loader, runtime, Python, UBQ, or CAS production edits.
Production/runtime/config/schema/migration delta is exactly ZERO.
The only T12-A paths are this document and the adjacent architecture test.
SBX-2 #163 is LANDED / CANONICAL / HEALTHY; SBX-3 #164 remains PRE-CLAIM HOLD.

## 1. Canonical logical identity and compatibility

The existing Tools V1 terminal logical exports are frozen:

- terminal.run@1.0 — synchronous bounded execution.
- terminal.launch@1.0 — detached start with pid, cwd, started result.
- physical tool terminal_tool version 2.0.0.
- Current public input schemas have command and optional cwd, with
  timeout/encoding only on terminal.run. No schema accepts an environment
  mapping, process token, raw process handle, sandbox lease, sandbox root,
  host PID-to-kill or isolation-disable switch.
- Public output schemas and existing failure/success ToolResult compatibility,
  metadata risk/effects, timeout limits and output limits remain frozen.
- Runtime-only env and process-ownership control MUST NOT be model-visible,
  added through **kwargs, public ToolResult, arbitrary caller arguments, or
  a new physical/logical terminal export without separate release.

Keep the current terminal.run hard timeout / bounded stdout-stderr / bounded
aggregate output and verified process-tree termination behavior; do not
reinterpret UBQ-5 #147 timeout taxonomy, introduce a new timeout alias, or
alter UBQ-4 #146 compute charging.

## 2. P1-T12-ENVIRONMENT-HANDOFF-1 — explicit spawn environment

Current source observation: tools/v1/terminal_tool.py
_platform_run_popen_kwargs and _platform_launch_popen_kwargs do not provide
an explicit subprocess environment; host ambient os.environ is inherited.
The existing _validate_cwd(None) resolves Path.cwd() (the host cwd).

Future production seam owner = Tools V1 / Issue #371 (NOT YET RELEASED).
An internal-only, immutable or copied environment mapping must be provided
at both run and launch spawn boundaries, after policy construction in the
lease-aware sandbox runtime. No ambient host secrets; no global mutation of
os.environ; no shell-specific env -i workaround; no public argument named
env/environ/environment in existing logical schemas. The environment
mapping must be allowlisted from SandboxProfile, with defined minimal
platform variables and no authority escalation from model input.

Issue #164 owns binding of the current SANDBOX invocation, path containment,
sandbox profile and isolation backend. Existing host Path.cwd() defaults are
not a sandbox boundary; server model-directed terminal default cwd MUST be
the verified sandbox root. Supplied cwd MUST resolve inside the lease.
No host-cwd or host-secret fallback is allowed when sandbox context is
missing or initialization fails: DENY / FAIL CLOSED.

## 3. P1-T12-LAUNCH-LEASE-OWNERSHIP-1 — stable process tree

Current source observation: terminal.launch starts detached/new-session Popen
and returns public pid/cwd/started but does not hand Popen/process identity
to a lease owner before acknowledging success. PID-only termination is
insufficient under process exit/PID reuse and cannot authorize arbitrary
host-process cleanup.

Future Tools V1-owned private launch seam MUST atomically hand off stable
process identity and tree-ownership authority to a lease-scoped registrar
before returning started=true; if registration fails, the entire launched
tree must be terminated/verified or the operation fails closed. The private
identity must be tied to Popen/process creation identity (or OS-scoped
equivalent), not a numeric PID alone; must never be serialized into a public
ToolResult, model input, persistent agent state, or sandbox:// file.

Issue #164 owns SandboxLease enrollment, lease cancellation/expiry handling,
bounded grace/kill/verification and OS-specific lifecycle cleanup. It MUST
verify identity before tree termination, never kill a reused/unrelated
host PID, and ensure terminal.launch process lifetime <= SandboxLease lifetime.
A created process that outlives a failed ownership handoff is a P1 blocker.
There is no detached durable background-job authority in this stage.

SandboxProfile policy data alone does not provide OS process-tree/memory/CPU
isolation. Enforced containment, counts, memory and CPU limits must be proved
on both Linux and Windows in the future production candidate.

## 4. P1-T12-PYTHON-LOGICAL-OWNERSHIP-1 — python.run@1.0

Canonical new logical ID: python.run@1.0, owned by Tools V1 / Issue #371;
it is a RESERVED FUTURE CAPABILITY, not currently registered or callable.

A future Tools V1 production PRE-CLAIM must independently own the logical
ID, physical implementation/module if required and bounded ToolResult,
schema, code input, execution, error and output contract before creation.
No authority to add tools/v1/python_tool.py is granted by T12-A.

Issue #164 owns EPHEMERAL_SANDBOX execution, lease isolation, SANDBOX target
routing, interpreter allowlist and SandboxProfile enforcement. A trusted
PythonCapabilityDriver in the server process is NOT an acceptable
implementation of model-directed python.run. No raw trusted-process SERVER
fallback: FAIL CLOSED. No silent CLIENT or alternate implementation fallback.

python.run default network mode = NONE; an allowed network override
requires independently frozen policy and enforced sandbox egress control,
not host-process conventions. Default environment excludes ambient secrets.
Bounds include memory/process/cpu, stdout/stderr/aggregate output, timeouts,
disk and generated-file containment; preserve UBQ ownership.

## 5. P1-T12-PYTHON-AUTO-DISCOVERY-ORDER-1 — atomic safe routing

Current source observation: local_tool_loader.register_local_tools
discovers eligible tools/v1/*.py carrying TOOL_METADATA plus callable run.
Its V2 plan creates PythonCapabilityDriver and can bind
server:<capability_id> if SERVER is eligible. Simply adding a discoverable
python_tool.py could expose server:python.run as trusted host Python.

Independent integration order MUST guarantee no intermediate canonical
main / deployed loader state where python.run can execute via ordinary
trusted-process SERVER routing. Two safe, separately audited options:

1. Implement and validate sandbox-only registration/routing guard first;
   only then land the Tools V1 logical/physical Python export with
   fail-closed discovery if sandbox context/profile is missing.
2. Land a tightly ordered dependency stack whose first atomic exposure
   occurs only after the sandbox-only guard is canonical; no mixed/partial
   rollout state may expose server:python.run.

Exact paths and PR parent/order must be decided at fresh production
PRE-CLAIM; neither option is authorized for code edits here. Missing
driver/context/profile must deny registration or invocation, never
fall back to raw SERVER. Demonstrate negative registration and invocation
cases as well as positive SANDBOX-only route on Linux and Windows.

## 6. Output, identity, persistence and isolation boundaries

- Server model-directed terminal.run, terminal.launch and python.run require
  a valid execution-scoped SANDBOX target and current SandboxLease.
- Sandbox process identity is execution/lease-scoped, not a reusable host
  process-kill token, model-supplied PID, or durable background job handle.
- Generated terminal/Python files remain sandbox:// ephemeral data until
  separately authorized explicit persistence (CAS bridge owned by #74/#166).
- A canceled, expired or restarted lease cannot be treated as a durable
  filesystem/process reference. No implicit upload, CAS mutation or replay.
- No host cwd traversal, host environment/secrets, unrestricted subprocess,
  ambient network egress or broad host PID cleanup on a failed boundary.
- Isolation claims require real OS/process/e2e evidence, not only a static
  SandboxProfile value or mock-only assertion.

## 7. Future owner split, ordered gates and exact production hold

Tools V1 #371 (future PRE-CLAIM, NOT RELEASED):
- Internal spawn env and stable launch identity/registrar seam in
  tools/v1/terminal_tool.py if independently required.
- New python.run@1.0 logical/physical bounded Tools contract and any new
  tools/v1/python_tool.py only after independent path-specific release.
- Preserve terminal logical schemas, risk/effects, timeout and ToolResult.

SBX-3 #164 (future PRE-CLAIM, NOT RELEASED):
- Lease-owned sandbox-aware runtime/driver/loader and SANDBOX target.
- Sandbox root/cwd validation, environment allowlist enforcement, OS process
  containment/cleanup and python network NONE default.
- No Tools V1 path edit without Tools-owned authority.

Mandatory sequence:
1. This T12-A exact two-path contract/evidence CLAIM + focused test.
2. Fresh Linux/Windows Architecture and independent T12-A FINAL.
3. Verify healthy then-current main, re-audit #147 UBQ, AE-R14
   AgentRuntime, #156/#164, Tools and relevant open ownership.
4. Obtain independent production PRE-CLAIM with exact pathset, parent/merge
   order and separate Tools/#164 authority; no implied ownership transfer.
5. Implement only separately CLAIMed production paths and negative/positive
   cross-platform isolation evidence, independent FINAL and integration wave.
6. Production merges only with their applicable explicit wave authorization.

Production PRE-CLAIM = HOLD / NOT RELEASED.
SBX-3 #164 production CLAIM = NONE.
Do not edit terminal_tool.py, any terminal behavior test, python_tool.py,
local_tool_loader.py, any runtime/driver/AgentRuntime path, UBQ #146/#147,
CAS #74/#166, web #165, clients, providers, schemas or migrations in T12-A.
A third path, expanded semantics or new blocking P0/P1 requires HOLD and a
fresh independent amendment.

## 8. T12-A acceptance and evidence limits

The adjacent architecture test mechanically asserts:
- exact terminal logical exports/versions, no model-visible env/identity
  controls, unchanged compatibility-relevant bounded Tool metadata;
- current source has process-tree lifecycle and current loader has the
  documented default SERVER discovery hazard;
- all four P1 seams have explicit fail-closed disposition and owner split;
- reserved python.run identity, default network NONE and no trusted fallback;
- lease-scoped identity, process cleanup, sandbox:// ephemeral storage;
- explicit production HOLD / PRE-CLAIM / FINAL / CI ordering and fences.

These are architecture/contract checks on the current source, not proof that
SBX-3 isolation or OS-level process containment is already implemented.
Neither Linux nor Windows baseline CI is production acceptance for SBX-3.
