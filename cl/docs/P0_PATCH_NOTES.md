# P0 Patch — UIBridge + console.js

## Scope
This patch targets the requested P0 priorities:

1. Workspace sandbox / filesystem boundary.
2. HITL approval correlation.
3. Conversation/session isolation.

It also hardens the JS renderer against the XSS path identified in the review and fixes the stream thought/text classification bug.

## Python — UIBridge drop-in replacement

### Workspace sandbox
- Canonical workspace root is computed once.
- Every read/write/copy/move/rename/delete path is canonicalized before use.
- Absolute paths outside the workspace are rejected.
- Symlink/junction-style escape through `realpath()` is rejected.
- Workspace root cannot be renamed/deleted/overwritten.
- `get_workspace_files()` does not recurse through symlinks.
- `paste_item()` uses `copytree(..., symlinks=True)` so directory-copy does not dereference symlinks into external locations.

### HITL
- Every approval gets a unique `approval_id`.
- Each approval has its own `threading.Event` and response slot.
- `respond_approval(choice, approval_id)` resolves exactly that request.
- Backward compatibility is kept when exactly one approval is pending and no ID is provided.
- Multiple pending approvals without an ID are rejected as ambiguous.
- A single UI approval bar is serialized with `_approval_ui_lock`.
- Approval timeout defaults to 300 seconds and fails closed (`False`).

### Session isolation
- `submit_prompt(..., conversation_id=None)` keeps the old call signature valid while allowing an explicit conversation/session key.
- Each conversation owns one `AgentContextSession`.
- Each conversation owns a lock, preventing concurrent mutations of the same session.
- Different conversations can execute concurrently.
- Each execution gets an `execution_id` for diagnostics and HITL payload correlation.

## JS — console.js

- Markdown output is sanitized before insertion into `innerHTML`.
- Dangerous HTML elements and event-handler attributes are removed.
- URLs are restricted to `http/https` (plus image data/blob where appropriate).
- File names and URL metadata are HTML-escaped before interpolation.
- `thought_stream` and `stream_content` are no longer both treated as thought text.
- Attachment downloads can decode `base64:<payload>` into a binary Blob.

## Important integration note
`showApprovalBar()` now receives `approval_id` (and, when available, `execution_id`) inside its payload. The approval UI should return that `approval_id` when calling the Python bridge `respond_approval(choice, approval_id)` for full multi-request correlation. The Python side remains backward-compatible for the single-pending case.

## Validation performed
- `python -m py_compile ui_bridge_p0.py` — pass.
- `node --check console_p0.js` — pass.
