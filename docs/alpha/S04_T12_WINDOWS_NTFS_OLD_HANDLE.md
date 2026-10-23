# S04 T12 — Windows NTFS old-identity open handle experiment

Issue #661, PR #671. T11 physical PASS confirmed POSIX existing writable
file descriptor survives later chmod. T12 tests the analogous Windows
question only on an isolated, disposable GitHub-hosted Windows 2025 runner.

**Scope:** a random local **unprivileged** account is created via NetUserAdd,
one Python subprocess is launched under its genuine token using
CreateProcessWithLogonW, and the account is deleted with NetUserDel after each
case. The generated password is passed in memory to the Win32 API only;
never to the command line, repository or CI log. Both code and SQLite data
are staged under an isolated randomized RUNNER_TEMP directory. Only this
fixture's NTFS ACL changes via icacls, never shared runner/workspace ACL.
Old child identity must match Win32 GetUserNameW. This is not an old binary.

| Mode × purpose | Required physical result |
|---|---|
| new_write × registration/reset | Old user initially granted modify; after explicit NTFS deny-W, a new process cannot open/write the SQLite file |
| restore × registration/reset | Old user cannot replace target SQLite snapshot after NTFS deny of directory write/delete-child |
| held_handle × registration/reset | **Negative control:** old user opens writable SQLite connection *before* NTFS cutover; after controller creates trigger + denies write, old can DROP trigger + DELETE using pre-existing handle |

T12 uses `PRAGMA journal_mode=MEMORY` to isolate file handle access from
new journal-file creation; it is only a diagnostic, not a deployed WAL
configuration. All account/file/process objects are removed in finally;
a failure to create real Windows token or remove the account is
**HARNESS_ERROR/INFRA**, never security PASS.

**Gate:** exact HEAD, 6 raw JUnit identities, zero failure/error/skip, only
fixtures and Windows 2025. A GREEN T12 demonstrates an OS-property/hazard,
not application authority. It does not prove old application process,
credential sharing, Windows real Host ACL, ACL inheritance/reparse bypass,
SQLite WAL, crash, HA, production users, or trusted old-process drain.
T05–T11 evidence and six mixed-version SEMANTIC_RED are unchanged. No
production code/migration edits, no release, merge/deploy or Codex Review.

## Required execution ordering (false-PASS guard)

New-process and stale-restore controls create their Win32 process **after**
NTFS deny ACEs are applied. The held-handle negative control must do the
reverse: open/attest the old writable SQLite handle, then add a SQLite
trigger and NTFS deny ACEs, then signal GO. A former T12 draft spawned all
three variants before cutover, introducing a scheduling race that could have
misclassified a pre-cutover write. No result from that draft may be used as
security proof; recheck exact-head CI on the corrected variant.
