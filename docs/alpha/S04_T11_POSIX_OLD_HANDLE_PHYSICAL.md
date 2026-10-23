# S04 T11 — real POSIX UID boundary and pre-opened SQLite file handles

Source: S04 T10 dual-OS physical PASS, which proved a trigger is not sufficient
to protect against an old writer with schema/file authority. This T11
investigation uses only synthetic, disposable `tempfile` SQLite databases.

Matrix executed in isolated subprocess as Linux `nobody`, distinct from the
owner/controller UID. `sudo -n -u nobody` is used only on a GitHub-hosted
Ubuntu runner and is checked physically; no production user, file or SID
is involved.

| Scenario | Expected physical property |
|---|---|
| new_connection × both purposes | After file chmod 0644, directory 0755, old UID cannot open new RW SQLite descriptor / cannot DELETE |
| stale_restore × both purposes | Old UID cannot replace the original SQLite path with stale pre-fence snapshot when parent directory prohibits rename |
| preexisting_handle × both purposes | **NEGATIVE CONTROL:** an old UID with a writable SQLite connection opened *before* chmod can DROP trigger and DELETE despite later chmod (MEMORY journal fixture) |

T11 specifically demonstrates why a correct handoff requires **drain and
terminate existing old writer processes/handles** in addition to denying new
open attempts. The chosen `PRAGMA journal_mode=MEMORY` makes SQLite use an
in-memory rollback journal for this controlled experiment so journal-file
creation failures cannot mask an already-open FD's write capability.
This mode is NOT the deployed journal mode and must not be used as a
production safety prescription.

Proof limits: no Windows NTFS ACL/SID, Linux ACL or namespaces beyond file
mode DAC, no inherited/shared writer credential, no old application binary,
no service process lifecycle, no real PostgreSQL/Redis HA, no immutable
production SQLite file, no owner-integrity parity, no cutover authorization.
The negative control does not fix product authority. Six mixed-authority
semantic REDs remain frozen and independent.

Do not modify production source, security oracle, schema, main, merge/deploy
or request Codex Review. A 6/6 PASS denotes **hazard reproduction**, not a
completed S04 security fix.

## Raw T11 harness finding at 2f82d616

CI #39816411436 reproduced **six S04_HARNESS_ERROR** cases, not security
RED: the old UID successfully ran `id`, but all child calls returned status
2 or failed to emit `READY`. Previous code launched a script from the GitHub
checkout, which can have parent-directory traverse restrictions for `nobody`.
The bounded harness repair stages its exact own script bytes into the
existing disposable directory with mode 0644 and leaves the other admission,
SQLite file, identity and security assertions intact. Any remaining failure
only reports a child **exception class** (no paths/secrets) for diagnosis;
this explanation is a hypothesis until exact-head CI confirms it. Neither the
historical old binary nor Windows ACL is covered.
