# assistant

**Repository:** boxs-51/assistant. This is a multi-component client/server Agent platform, not a single Python script.

## Start here
- **Current non-production alpha truth:** [Alpha canonical index](docs/alpha/CANONICAL_INDEX.md)
- **Observed architecture and boundaries:** [Current architecture](docs/alpha/CURRENT_ARCHITECTURE.md)
- **One-step-at-a-time delivery:** [Linear delivery sequence](docs/alpha/LINEAR_DELIVERY_SEQUENCE.md)
- **Open technical risks and evidence:** [Defect ledger](docs/alpha/DEFECT_LEDGER.md)
- **Documentation authority and legacy mapping:** [Document status matrix](docs/alpha/DOCUMENT_STATUS_MATRIX.md)

## Code map
| Location | Responsibility |
|---|---|
| se/src/main.py | FastAPI/server runtime composition |
| se/src/transport/gateway | HTTP, identity, client connection |
| se/src/runtimes/agent | durable Agent execution, recovery and resume |
| se/src/runtimes/capability | logical tool/capability routing and sandbox interfaces |
| se/src/application, se/src/infrastructure/storage | ownership/policies, databases, Alembic |
| cl/src/main.py, cl/src/core, cl/src/ui | desktop client, gateway, HITL, UI |
| tools/v1, se/tests, cl/tests | tools and regression suites |

The **DIRECT** compatibility path and **AGENT** execution path currently coexist in source. The target Agent-only roadmap is not evidence of a completed cutover.

## Development and testing (source reference, NOT a verified setup recipe)
- The CI workflow .github/workflows/architecture-baseline.yml selects Python 3.12.10 and installs requirements-test.txt.
- Its Linux job uses: python -m pytest -q. Its Windows client job uses: python -m pytest -q cl/tests.
- Local prerequisites, environment variables, migrations, Redis/PostgreSQL 17, actual OS-native confinement, and a safe database must be prepared and verified before running integration tests.
- docker-compose.yml declares gateway, Redis and PostgreSQL 17 services, but compose launch, migrations and real deployment success have **not** been verified in this documentation pass. Inspect .env configuration locally; never commit credentials.
- At verified `alpha@300801d67c43d702408d2895f7c89898e27a87fd`, revision `32a_alpha_31b_31t_merge` joins the formerly independent 31b/31t lineages and forward-only `32b_alpha_s03_owner_integrity` follows. **Do not run `alembic upgrade head` on live data merely because the source graph is reconciled**: target database revision, grants, backup and environment-specific rollback must be checked. No production upgrade is certified. See [S02 #659](https://github.com/boxs-51/assistant/issues/659) and [S03 #660](https://github.com/boxs-51/assistant/issues/660).

## Branch and evidence policy
- **main:** do not merge or deploy as part of alpha development.
- **alpha:** non-production integration and documentation baseline; may contain intentionally failing security tests.
- **dev branches:** one bounded issue/stage at a time, only after that stage's prerequisite gates are checked. Keep original immutable test assertions, P0/P1 and SECURITY HOLD.
- A narrow green CI job does **not** prove full product, production security or native OS authorization.
- S03 [PR #668](https://github.com/boxs-51/assistant/pull/668) passed 12 workflows on its exact PR HEAD and merged to `alpha@300801d6`. [Post-merge PG17](https://github.com/boxs-51/assistant/actions/runs/39535525959) reported 58 PASS per OS; the full S03/Architecture suite was **not** rerun on the merge SHA. Automatic Codex **code** review found no issues; no Codex security review was run. Full integration stays open in [#655](https://github.com/boxs-51/assistant/issues/655).

For historical designs see docs/ROADMAP_NAMESPACE_REGISTRY.md and docs/alpha-integration-ledger-20261018.md. Repository Policy #85/M1-M7 is RETIRED; technical security contracts remain in force independently of retired governance text.
