# AKS Upgrade Agent POC — Progress Tracker

> Living document. Updated whenever a meaningful change is implemented, validated, or troubleshot.
> Status reflects **actual verified state**, not intent. See status legend below.

**Status legend**
- 🟢 DONE — implemented AND validated/tested
- 🟡 IN PROGRESS — implementation exists or work is underway but validation is incomplete
- 🔴 NOT STARTED / MISSING — required work has not been implemented
- ⚠️ BLOCKED — work cannot proceed because of a known blocker

Last updated: 2026-08-31 (deprecated API tool added)

---

## 1. POC Objective

We inherited a partially-built "AKS Upgrade Agent": a Microsoft Foundry-hosted AI agent that talks to an MCP (Model Context Protocol) server, which in turn inspects a real, existing AKS cluster and reports on whether it is safe to upgrade. In plain terms: **before someone clicks "upgrade" on a production AKS cluster, this agent should be able to explain what will break and why** — unhealthy nodes/pods, PodDisruptionBudget conflicts, storage problems, deprecated Kubernetes APIs, etc. — and only allow a real upgrade to proceed once those risks are addressed or explicitly accepted.

We are **not** creating a new AKS cluster for this POC; we assess and (eventually) act on an existing one.

## 2. Architecture

### Current implementation (as of this assessment)
```
Foundry Agent (main.py, agent-framework-agent-with-foundry-toolbox-responses/src/...)
    ↓ (TOOLBOX_ENDPOINT / AKS_MCP_ENDPOINT env var, via toolbox.yaml)
AKS Operations MCP Server — TWO parallel entrypoints exist (architecture duplication, not yet resolved):
    - src/aks-operations-mcp/main.py        → intended for Azure Container Apps (azd host.containerapp)
    - src/aks-operations-mcp/function_app.py → legacy Azure Functions entrypoint (infra/main.bicep still provisions this by default)
    ↓ (azure-mgmt-containerservice + AKS Run Command API, using DefaultAzureCredential / user-assigned managed identity)
Existing AKS Cluster (not created by this project; referenced via `existingAksClusterResourceId`)
    ↓
kubectl (via AKS Run Command) → node/pod/pdb/storage JSON → classified into blockers/warnings
```

### Target architecture (per current direction)
```
Foundry Agent
    ↓
MCP Server (Azure Container Apps ONLY — Function App path to be retired)
    ↓
Managed Identity (user-assigned, dedicated to the Container App)
    ↓
Azure RBAC / AKS access (Reader + custom AKS Run Command role; write-path RBAC for real upgrades still unresolved)
    ↓
Existing AKS Cluster
```

**Key gap between current and target:** the Azure Functions hosting path (`function_app.py`, Function App/Storage/Plan/Log Analytics/App Insights in `infra/main.bicep`) is still fully provisioned by default (`deployAksMcpFunction = true`) even though the target is Container Apps-only. This has not yet been removed. See [Section 10](#10-infrastructure--deployment).

## 3. Phase Plan

| Phase | Status | Objective |
|---|---|---|
| 1 — Initialize Foundry Agent | 🟡 IN PROGRESS | Stand up the Foundry-hosted agent shell |
| 2 — Build AKS MCP Server | 🟡 IN PROGRESS | Implement AKS operational tools behind MCP (10/10 tools now implemented; validation still incomplete) |
| 3 — Connect to Existing AKS | 🟡 IN PROGRESS | Wire identity/RBAC to the existing cluster |
| 4 — Provision/Deployment | 🟡 IN PROGRESS | Bicep + azd provisioning of supporting resources |
| 5 — MCP Completion & Validation | 🟡 IN PROGRESS | All required tools implemented and tested |
| 6 — AKS Identity & RBAC Validation | 🟡 IN PROGRESS | Confirm least-privilege access actually works |
| 7 — Container Apps Deployment | 🟡 IN PROGRESS | MCP running remotely on Container Apps |
| 8 — Foundry ↔ MCP Integration | 🔴 NOT STARTED | Agent actually calling the deployed MCP endpoint |
| 9 — Pre-Upgrade Assessment | 🟡 IN PROGRESS | Composite readiness check across all risk areas |
| 10 — Intentional Failure Testing | 🔴 NOT STARTED | Demonstrate detection against real induced failures |
| 11 — End-to-End Validation | 🔴 NOT STARTED | Full path proven against the real cluster |
| 12 — Demo & Documentation | 🔴 NOT STARTED | Present the POC |

Detail per phase:

### Phase 1 — Initialize Foundry Agent
- **Status:** 🟡 IN PROGRESS (inherited, refined by us)
- **Objective:** Foundry-hosted agent shell that can call MCP tools.
- **Completed:** Agent scaffold (`main.py`) existed already (inherited). We replaced the generic placeholder instructions with an AKS Upgrade Agent persona (2026-08-30).
- **Remaining:** No confirmed live Foundry deployment/run of the agent in this environment; instructions don't yet reference all 9 available tools (e.g., storage, readiness, upgrade explicitly).

### Phase 2 — Build AKS MCP Server
- **Status:** 🟡 IN PROGRESS
- **Objective:** Expose AKS operational checks as MCP tools.
- **Completed:** 10 tools now implemented and registered identically in both `main.py` and `function_app.py`: `aks_get_cluster_details`, `aks_get_node_pools`, `aks_get_available_upgrades`, `aks_check_node_health`, `aks_check_pod_health`, `aks_check_pdb`, `aks_validate_upgrade_readiness`, `aks_upgrade_node_pool`, `aks_check_storage`, `aks_check_deprecated_apis` (added 2026-08-31).
- **Remaining:** No tool is yet validated against a real cluster or confirmed via an executed pytest run in this session. `aks_check_deprecated_apis` has unit tests written (mocked kubectl) but execution is unconfirmed here — see Section 7.

### Phase 3 — Connect to Existing AKS
- **Status:** 🟡 IN PROGRESS (inherited groundwork)
- **Objective:** Point the MCP server at the real, existing AKS cluster.
- **Completed:** `infra/main.bicep` takes `existingAksClusterResourceId` and never provisions a new cluster. Kubernetes RBAC bootstrap scripts (`bootstrap-k8s-rbac.ps1/sh`) exist to grant in-cluster read access.
- **Remaining:** No evidence in-repo that the bootstrap scripts were actually run against the real cluster, or that connectivity was confirmed end-to-end.

### Phase 4 — Provision/Deployment
- **Status:** 🟡 IN PROGRESS (inherited + modified)
- **Objective:** Provision supporting Azure resources.
- **Completed:** Bicep defines two user-assigned identities, RBAC modules, and (legacy) Function App hosting resources.
- **Remaining:** No captured evidence of a successful `azd up`/`azd provision` run in this repo. Legacy Function App resources still deploy by default and need a decision (remove vs. keep for now).

### Phase 5 — MCP Completion & Validation
- **Status:** 🟡 IN PROGRESS
- **Objective:** All required tools implemented and unit/integration tested.
- **Completed:** Unit tests exist for storage classification (8 tests) and readiness/storage integration (5 tests) — see [Section 7](#7-mcp-tool-inventory).
- **Remaining:** No tests exist for discovery, validation (node/pod/pdb), or the upgrade write path. Deprecated API tool missing entirely. `python -m pytest` has not been executed and confirmed from this workstation (Python isn't installed here); a prior VM run left compiled `__pycache__` artifacts in git history but no captured pass/fail console output is available to us.

### Phase 6 — AKS Identity & RBAC Validation
- **Status:** 🟡 IN PROGRESS
- **Objective:** Prove least-privilege identities actually work against the real cluster.
- **Completed:** Reader + custom "AKS Run Command" role (`Microsoft.ContainerService/managedClusters/runcommand/action` + `.../commandResults/read`) defined and assigned in Bicep for the Container App identity.
- **Remaining:** Write-path RBAC for `aks_upgrade_node_pool`'s real (non-dry-run) `agent_pools` write was intentionally left unresolved (exact ARM action not confidently verified — documented decision, not an oversight). No confirmation these role assignments were actually applied and exercised against the live cluster.

### Phase 7 — Container Apps Deployment
- **Status:** 🟡 IN PROGRESS
- **Objective:** MCP server running remotely on Azure Container Apps.
- **Completed:** `azure.yaml` defines `aks-mcp` as a `host.containerapp` service. A script (`configure-aks-mcp-container-identity.ps1`) exists to attach the dedicated managed identity to an **already-deployed** Container App.
- **Remaining:** No confirmation in this repo that the Container App has actually been deployed and is reachable; the identity-attach script assumes it already exists.

### Phase 8 — Foundry ↔ MCP Integration
- **Status:** 🔴 NOT STARTED
- **Objective:** The Foundry agent actually calls the deployed MCP tools live.
- **Completed:** `toolbox.yaml` has an `aks-mcp` entry wired up (no-auth pattern).
- **Remaining:** `server_url` is still the literal placeholder `<AKS_MCP_ENDPOINT>` — never replaced with a real endpoint. No evidence of a live toolbox creation (`azd ai toolbox create`) or a real agent-to-MCP call.

### Phase 9 — Pre-Upgrade Assessment
- **Status:** 🟡 IN PROGRESS
- **Objective:** One composite readiness check spanning all risk areas.
- **Completed (2026-08-30/31):** `aks_validate_upgrade_readiness` now aggregates node health, pod health, PDB health, storage health, and (new, 2026-08-31) deprecated/removed Kubernetes API findings, plus maintenance-window checks, into a single blockers/warnings result. When called from `aks_upgrade_node_pool`, the real requested `kubernetes_version` is now passed through as the deprecated-API check's target version (not invented/guessed).
- **Remaining:** Does not yet incorporate available-upgrade information (`aks_get_available_upgrades` is still a separate, uncorrelated tool call). No live-cluster validation of the combined readiness result.

### Phase 10 — Intentional Failure Testing
- **Status:** 🔴 NOT STARTED
- **Objective:** Prove detection using deliberately broken scenarios on the real cluster.
- **Completed:** Only synthetic/mocked unit tests exist (fabricated Python dicts, not real cluster manifests).
- **Remaining:** No real PDB-eviction-conflict, PV/PVC failure, deprecated-API, unhealthy-pod, or node-health scenario has been deployed to the actual cluster and observed by the agent.

### Phase 11 — End-to-End Validation
- **Status:** 🔴 NOT STARTED
- **Objective:** Full path (Foundry → MCP → real AKS → explained result) proven at least once.
- **Remaining:** Blocked on Phase 8 (real endpoint) and Phase 10 (real test scenarios).

### Phase 12 — Demo & Documentation
- **Status:** 🔴 NOT STARTED
- **Objective:** Present the POC with evidence.
- **Completed:** This tracking document is being created now.
- **Remaining:** Everything else.

## 4. Current Status

- **Current phase:** Phase 9 (Pre-Upgrade Assessment) is the most advanced; Phase 2/5 (tool completeness) is the current blocker for moving further.
- **Current task:** Confirm the newly-written unit tests (14 total: 9 for `aks_check_deprecated_apis`, 1 new readiness-integration test) actually pass via a real pytest run on the VM; then move to Phase 8 wiring.
- **Overall POC status:** All 10 planned MCP tools are now implemented and unit-tested with mocked/synthetic data. Nothing has been validated end-to-end against the real Foundry agent, the real deployed MCP endpoint, or the real AKS cluster from within this workspace/session.
- **What is working:** 10 of the planned MCP tools are implemented with matching schemas in both server entrypoints; readiness check now folds in storage health AND deprecated/removed API health; storage and deprecated-API classification logic both have dedicated unit tests.
- **What is incomplete:** live endpoint wiring (`toolbox.yaml` placeholder), confirmed RBAC-in-practice, real cluster test scenarios, and any full end-to-end run. No unit tests yet for `tools/discovery.py` or `tools/validation.py`.
- **What we are doing next:** Get real pytest output from the VM for the full suite; then revisit Phase 8 wiring.

## 5. Completed Work

| Date | Task | Description | Files changed | Validation performed | Result |
|---|---|---|---|---|---|
| 2026-08-30 | Phase 2 Task 1 | Added `aks-mcp` entry to `toolbox.yaml` (placeholder endpoint); replaced generic Foundry agent instructions with AKS Upgrade Agent persona | `toolbox.yaml`, `main.py` (Foundry agent) | Diagnostics only (`get_errors`); terminal was non-responsive this session | Implemented, not live-validated |
| 2026-08-30 | Phase 2 Task 2 | `function_app.py` now imports and exposes `aks_validate_upgrade_readiness`/`aks_upgrade_node_pool` from `tools/upgrade.py` (previously missing from the Function entrypoint) | `function_app.py` | Diagnostics only | Implemented, not live-validated |
| 2026-08-30 | Phase 2 Task 4 | Verified via Microsoft Learn docs that AKS Run Command needs a custom role (no built-in role covers it); added custom role Bicep + a new dedicated Container App identity; added identity-attach script with env-var-merge safety fix | `infra/modules/aks-run-command-role.bicep`, `infra/main.bicep`, `tools/common.py`, `scripts/configure-aks-mcp-container-identity.ps1` | Documentation review (Microsoft Learn); diagnostics only, no live Azure validation | Implemented, not deployed/validated |
| 2026-08-30 | Security review | Fixed a real bug: identity-attach script was replacing (not merging) Container App env vars, risking wiping unrelated settings | `scripts/configure-aks-mcp-container-identity.ps1` | Code review/trace only (no live container app to test against) | Fixed, not live-validated |
| 2026-08-30 | Phase 2 Task 5 | Implemented `aks_check_storage` (9th tool): PVC/PV/StorageClass/pod/event classification with context-aware severity rules | `tools/storage.py`, `main.py`, `function_app.py`, `tests/test_storage.py`, `tests/conftest.py`, `requirements.txt`, `README.md` | 8 unit tests written; execution not confirmed from this workstation (no Python here) — validated via manual line-by-line trace against implementation | Implemented; unit-test pass unconfirmed by direct execution in this session |
| 2026-08-30 | Phase 2 Task 6 | Integrated `aks_check_storage` into `aks_validate_upgrade_readiness` (storage blockers → readiness blockers, storage warnings → readiness warnings, only in `check_mode="full"`) | `tools/upgrade.py`, `tests/test_upgrade_readiness.py` | 5 new unit tests written (mocked node/pod/pdb, real storage classification functions); `get_errors` diagnostics clean; pytest not executed from this workstation | Implemented; unit tests written but execution not confirmed in this session |
| 2026-08-30 (VM) | Test execution | Commits `for testcases` and `updates from vm` show `test_upgrade_readiness.py` was added and Python `__pycache__` artifacts for `test_storage`/`test_upgrade_readiness`/`conftest` were generated using `pytest-9.1.1` (implies the suite was collected/run on the VM) | (bytecode only, no source changes in the second commit) | Indirect: presence of pytest bytecode cache implies execution occurred | **Not confirmed** — no captured console pass/fail output exists in the repo; cannot claim tests passed based on `__pycache__` alone |
| 2026-08-31 | Phase 5: `aks_check_deprecated_apis` | Implemented deprecated/removed Kubernetes API detection tool; integrated into `aks_validate_upgrade_readiness`; registered in both MCP entrypoints | `tools/deprecated_apis.py` (new), `tools/upgrade.py`, `main.py`, `function_app.py`, `README.md`, `tests/test_deprecated_apis.py` (new), `tests/test_upgrade_readiness.py` | `get_errors` diagnostics clean on all changed files; 9 new unit tests written for the tool + 1 new readiness-integration test; **pytest execution not possible from this workstation** (Python not installed — confirmed via Windows Store execution-alias error) | Implemented; validation incomplete — marked 🟡 IN PROGRESS, not 🟢 DONE |

## 6. Current Work

- Awaiting a real pytest execution (on the VM, where Python is installed) covering the full suite, including the 10 new tests added for `aks_check_deprecated_apis` and readiness integration.
- No other code change is in flight as of this update.

## 7. MCP Tool Inventory

| Tool | Purpose | Implementation Status | Validation Status |
|---|---|---|---|
| `aks_get_cluster_details` | Cluster metadata/current version | 🟢 DONE | 🔴 NOT TESTED (no unit tests; no live-cluster run confirmed) |
| `aks_get_node_pools` | Node pool inventory | 🟢 DONE | 🔴 NOT TESTED |
| `aks_get_available_upgrades` | Available K8s/node-image upgrade paths | 🟢 DONE | 🔴 NOT TESTED |
| `aks_check_node_health` | Node Ready/pressure conditions | 🟢 DONE | 🔴 NOT TESTED (only exercised via mocked stubs in readiness tests, not its own logic) |
| `aks_check_pod_health` | Pod phase/restarts/waiting reasons | 🟢 DONE | 🔴 NOT TESTED |
| `aks_check_pdb` | PodDisruptionBudget disruption risk | 🟢 DONE | 🔴 NOT TESTED |
| `aks_validate_upgrade_readiness` | Composite readiness check (node+pod+pdb+storage+maintenance window) | 🟢 DONE (storage now integrated) | 🟡 IN PROGRESS (5 mocked unit tests written; execution not confirmed in this session) |
| `aks_upgrade_node_pool` | Guarded node pool upgrade execution | 🟢 DONE (dry-run default, write gated) | 🔴 NOT TESTED (no unit tests exist for this function) |
| `aks_check_storage` | PVC/PV/StorageClass/pod/event storage health | 🟢 DONE | 🟡 IN PROGRESS (8 unit tests written; pytest execution not confirmed from this workstation) |
| `aks_check_deprecated_apis` | Detect deprecated/removed Kubernetes API usage vs. a target version | 🟢 DONE | 🟡 IN PROGRESS (9 unit tests written, mocked kubectl responses; pytest execution not confirmed from this workstation; no real-cluster validation performed) |

## 8. AKS Upgrade Risk Coverage

| Risk | Detection Implemented | Tested | Notes |
|---|---|---|---|
| Node health | 🟢 | 🔴 | `aks_check_node_health`; no direct unit test |
| Pod health | 🟢 | 🔴 | `aks_check_pod_health`; no direct unit test |
| PDB | 🟢 | 🔴 | `aks_check_pdb`; no direct unit test |
| PV/PVC | 🟢 | 🟡 | Classification logic unit-tested (`test_storage.py`); execution unconfirmed in this session |
| Storage (mount/attach/provisioning failures) | 🟢 | 🟡 | Same as above; also now feeds into readiness (`test_upgrade_readiness.py`) |
| Deprecated/removed Kubernetes APIs | � | 🟡 | `aks_check_deprecated_apis` (2026-08-31): targeted `kubectl get <plural>.<version>.<group>` queries against a maintained, documented deprecation matrix (16 entries, sourced from the official Kubernetes deprecation guide); integrated into readiness. Unit-tested with mocked kubectl; no real-cluster validation |
| Upgrade availability | 🟢 | 🔴 | `aks_get_available_upgrades`; not yet correlated with readiness output |
| Maintenance window | 🟢 | 🔴 | Implemented inline in `tools/upgrade.py`; no dedicated unit test |

## 9. Identity & RBAC

- **Managed identities (Bicep):**
  - `aksMcpIdentity` — original identity, attached to the legacy Function App.
  - `aksMcpContainerAppIdentity` — dedicated identity added for the Container App path (kept separate so Function RBAC wasn't touched).
- **Azure RBAC:**
  - Function identity: Reader + "Azure Kubernetes Service Cluster User Role" (built-in GUIDs), scoped to the existing AKS cluster resource.
  - Container App identity: Reader (built-in) + a **custom** role granting exactly `Microsoft.ContainerService/managedClusters/runcommand/action` and `.../commandResults/read` (verified against Microsoft Learn docs — no built-in role grants only these actions).
- **Kubernetes access:** `bootstrap-k8s-rbac.ps1/sh` scripts define a ClusterRole/ClusterRoleBinding for read-only access to nodes/pods/namespaces/PDBs — presence of the script is confirmed; actual execution against the real cluster is **not confirmed** in this repo.
- **AKS Run Command permissions:** Custom role covers only the run-command actions needed for read-only kubectl checks. Write permissions for real (non-dry-run) `aks_upgrade_node_pool` (`agent_pools` write action) are **intentionally not yet granted** — the exact ARM action string was not confidently verified from an authoritative source, so it was left unresolved rather than guessed. Real upgrades will fail authorization until this is verified and added.
- **Least privilege considerations:** Deliberately split identities per hosting path; custom role scoped narrowly instead of reusing a broad built-in role.
- **What has been tested:** Nothing — no `az role assignment list` output, no live run-command call, and no confirmation that either identity has actually been used against the real cluster from within this session.
- No secrets, tokens, or credentials are recorded in this document or the repo (confirmed via review).

## 10. Infrastructure / Deployment

- **Bicep (`infra/main.bicep`):** Defines both identities, both RBAC paths, and (still, by default) full Azure Functions hosting resources (Storage Account, Consumption Plan, Function App, Log Analytics, Application Insights) — this is the **legacy path** per the target architecture.
- **Azure Container Apps:** Declared in `azure.yaml` (`af-foundry-agent`, `aks-mcp` as `host.containerapp`), but **no Bicep persists the Container App resources themselves** — `azd` generates/manages them in memory (per `next-steps.md`). No confirmation a `azd up`/`azd provision` has been run and succeeded in this environment.
- **Azure Container Registry:** Not defined explicitly in Bicep; `remoteBuild: true` implies `azd` manages image build/push itself. Not independently verified.
- **Foundry resources:** Managed via `infra: provider: microsoft.foundry` in the nested `azure.yaml` — no persisted Bicep for this either; relies on `azd`'s Foundry provider.
- **MCP endpoint:** Real Container App URL is still an unresolved placeholder (`<AKS_MCP_ENDPOINT>`) in `toolbox.yaml`. The historically-referenced endpoint pattern (`https://aks-mcp.<env>.<region>.azurecontainerapps.io/mcp`) appears in scripts/docs but a live, current URL is not confirmed here.
- **Monitoring:** Log Analytics + Application Insights are wired **only** to the legacy Function App path. No monitoring is currently wired to the Container App path in Bicep.
- **Existing Function App code/infra still needing removal or replacement:** `function_app.py` (Azure Functions entrypoint) and the corresponding Bicep resources (`aksMcpFunctionStorage`, `aksMcpFunctionPlan`, `aksMcpFunctionApp`, `aksMcpLogAnalytics`, `aksMcpAppInsights`, `deployAksMcpFunction` parameter and its role assignment module) — these represent the architecture-duplication risk called out in Phase 1 audit and have not yet been removed.

## 11. Testing

| Test | Expected Result | Actual Result | Status |
|---|---|---|---|
| Healthy storage (Bound PVC + Bound PV + Running pod) | `HEALTHY`, no blockers | Unit test asserts this (`test_storage.py::test_healthy_storage`) | 🟡 written, execution unconfirmed |
| Pending PVC referenced by an active pod | `BLOCKER` | Unit test asserts this (`test_storage.py::test_pending_pvc_has_clear_reason`) | 🟡 written, execution unconfirmed |
| Failed mount / volume attach | Detected as storage failure | Unit tests assert this (`test_failed_mount_is_detected`, `test_failed_volume_attachment_event_is_detected`) | 🟡 written, execution unconfirmed |
| Orphaned/unclaimed PV | `WARNING`, never a blocker | Unit test asserts this (`test_orphaned_pv_is_warning_not_blocker`) | 🟡 written, execution unconfirmed |
| Unrelated pod failure (ImagePullBackOff) | NOT classified as storage issue | Unit test asserts this (`test_unrelated_pod_failure_is_not_a_storage_problem`) | 🟡 written, execution unconfirmed |
| Storage blockers propagate into readiness | `aks_validate_upgrade_readiness` blocked | Unit test asserts this (`test_upgrade_readiness.py::test_pending_pvc_referenced_by_pod_blocks_readiness`, `test_failed_mount_blocks_readiness`) | 🟡 written, execution unconfirmed |
| Storage warnings do not block readiness | `is_ready=True`, warning present | Unit test asserts this (`test_orphaned_pv_warns_but_does_not_block_readiness`) | 🟡 written, execution unconfirmed |
| No deprecated APIs in use for target version | `HEALTHY`, no findings | Unit test asserts this (`test_deprecated_apis.py::test_no_deprecated_apis_detected`) | 🟡 written, execution unconfirmed |
| Deprecated-but-still-served API detected | `WARNING`, not blocked | Unit test asserts this (`test_deprecated_api_still_served_is_a_warning`) | 🟡 written, execution unconfirmed |
| Removed-in-target API detected | `BLOCKER` | Unit test asserts this (`test_removed_api_in_target_is_a_blocker`) | 🟡 written, execution unconfirmed |
| Multiple deprecated/removed API findings | All reported together | Unit test asserts this (`test_multiple_findings_across_api_versions`) | 🟡 written, execution unconfirmed |
| Missing target version | Falls back to cluster's current version, no error | Unit test asserts this (`test_missing_target_version_falls_back_to_cluster_current_version`) | 🟡 written, execution unconfirmed |
| Invalid target version format | Raises `ValueError` | Unit test asserts this (`test_invalid_target_version_raises`) | 🟡 written, execution unconfirmed |
| Deprecated API findings propagate into readiness | Readiness blocked when a removed-API finding exists | Unit test asserts this (`test_upgrade_readiness.py::test_deprecated_api_findings_are_included_in_readiness`) | 🟡 written, execution unconfirmed |
| PDB disruption/eviction constraint (real cluster) | Detected as blocker | Not attempted | 🔴 NOT STARTED |
| Deprecated API usage (real cluster) | Detected and explained | Not attempted (unit-tested only, no real cluster) | 🔴 NOT STARTED |
| Unhealthy pod (real cluster) | Detected as blocker | Not attempted | 🔴 NOT STARTED |
| Node health problem (real cluster) | Detected as blocker | Not attempted | 🔴 NOT STARTED |

> Note on "execution unconfirmed": diagnostics (`get_errors`) are clean and code has been manually traced, but no session in this workspace has been able to run Python (not installed on this PC). A VM-side commit (`for testcases`, `updates from vm`) shows pytest bytecode artifacts consistent with a run having occurred, but no captured pass/fail console output is available to verify results.

## 12. Problems / Blockers

| Date | Problem | Impact | Investigation | Resolution | Current Status |
|---|---|---|---|---|---|
| 2026-08-30 | `configure-aks-mcp-container-identity.ps1` used `az containerapp update --set-env-vars`, which replaces the entire env var list | Could silently wipe unrelated Container App configuration | Confirmed via known `az` CLI behavior | Rewrote script to fetch existing env vars, merge in the 4 managed vars by key, and pass the full merged list back | 🟢 Fixed (not live-validated against a real container app) |
| 2026-08-30 | Write-path RBAC action for `agent_pools` upgrades not confidently verified | Real (non-dry-run) node pool upgrades will fail authorization | Checked built-in role catalog; no authoritative source found for the exact write action in this session | Deliberately left unresolved rather than guessing, per explicit instruction | ⚠️ BLOCKED — needs an authoritative source before granting write RBAC |
| Ongoing | Python is not installed on this work PC | Cannot execute `pytest` or validate runtime behavior from this workstation | Confirmed via Windows Store execution-alias error when invoking `python` | User runs tests on a separate VM | ⚠️ BLOCKED (workstation-specific; not a code issue) |
| 2026-08-30 | Two parallel MCP hosting paths (`main.py` for Container Apps, `function_app.py` for Azure Functions) exist with historically different tool sets | Confusion about which is authoritative; doubles maintenance surface | Confirmed both now expose identical 9 tools, but legacy Function infra still provisions by default | Not yet resolved — pending decision to retire the Function App path | 🟡 Open |
| 2026-08-30 | `toolbox.yaml`'s `aks-mcp` entry has a literal placeholder `server_url` | Foundry agent cannot actually reach the MCP server until this is set | Confirmed via direct file read | Not yet resolved | 🔴 Open |
| Repo hygiene | `updates from vm` commit added compiled `__pycache__` bytecode files to source control | Repo noise; `.gitignore` only excludes `.azure` | Confirmed via `git show --stat` | Not yet fixed | 🟡 Open (minor, no functional impact) |

## 13. Technical Decisions

- **Existing AKS cluster will be used** — no new cluster is created by this POC; all Bicep/RBAC targets `existingAksClusterResourceId`.
- **MCP hosting direction is Azure Container Apps** — the Function App path is legacy/inherited and targeted for eventual removal, not further investment.
- **Managed Identity will be used instead of static credentials** — `DefaultAzureCredential`, optionally pinned via `AZURE_CLIENT_ID`, used throughout `tools/common.py`.
- **A dedicated identity was created for the Container App path** rather than reusing the Function App's identity, so RBAC changes for one path never risk affecting the other.
- **A custom RBAC role was created for AKS Run Command** rather than assigning a broader built-in role, after confirming no built-in role grants exactly the required actions (documented in Section 9).
- **Write-path RBAC for real upgrades is intentionally withheld** until the exact required ARM action can be verified from an authoritative source — "don't guess" was an explicit instruction.
- **Storage health detection lives only in `tools/storage.py`** — `aks_validate_upgrade_readiness` calls it and maps its output; no duplicate classification logic was added to `tools/upgrade.py`.
- **Existing upgrade safety gates are non-negotiable and were preserved as-is** through the storage integration: `dry_run=True` default, `check_mode='full'` required for writes, `AKS_UPGRADE_ENABLE_WRITE` env gate, approval-token check, and readiness-precheck-before-write.
- **POC will intentionally test PDB/PV-PVC/deprecated-API/unhealthy-pod/node-health failures** — currently only PV/PVC-style logic has any test coverage (synthetic, not live-cluster).
- **Real upgrade execution remains protected** until pre-upgrade assessment is proven reliable — enforced today via the existing gates listed above.
- **Deprecated-API detection uses targeted `kubectl get <plural>.<version>.<group>` queries against a small, explicitly-maintained matrix** (16 entries, sourced from the official Kubernetes deprecation guide) rather than an exhaustive historical compatibility database, and rather than trusting `kubectl api-resources` alone (which only reflects what the *current* server serves, not what will be removed by a future target version).
- **When `target_version` is not supplied to `aks_check_deprecated_apis`, it falls back to the cluster's own current `kubernetes_version`** (via `aks_get_cluster_details`) rather than inventing or guessing an upgrade target. When invoked internally from `aks_upgrade_node_pool` (via `aks_validate_upgrade_readiness`), the real requested `kubernetes_version` is passed through automatically, so the check is meaningful for that specific upgrade attempt without guessing.

## 14. Change Log

### 2026-08-30 — AKS upgrade MCP capabilities (Phase 2 Tasks 1, 2, 4, 5 + security fix)
**Phase:** 2, 4, 6
**Task:** Toolbox wiring, Function/Container tool parity, RBAC for AKS Run Command, `aks_check_storage`, env-var-merge bug fix
**Change:** Added `aks-mcp` entry to `toolbox.yaml`; rewrote Foundry agent instructions; synced `function_app.py` tool registry with `main.py`; added dedicated Container App managed identity + custom AKS Run Command RBAC role; implemented `aks_check_storage` (9th tool) with 8 unit tests; fixed an env-var-wipe bug in the identity-attach script
**Files:** `toolbox.yaml`, Foundry `main.py`, `function_app.py`, `infra/main.bicep`, `infra/modules/aks-run-command-role.bicep`, `tools/common.py`, `tools/storage.py`, `tests/test_storage.py`, `tests/conftest.py`, `requirements.txt`, `README.md`, `scripts/configure-aks-mcp-container-identity.ps1`
**Reason:** Complete Phase 2 tool set; establish least-privilege RBAC for the Container App path; prevent accidental config loss
**Validation:** Diagnostics (`get_errors`) clean; manual line-by-line trace of all 8 storage tests; no live Azure/pytest execution in this session
**Result:** Implemented, committed, pushed to `origin/main` (commit `ab9e242`)
**Next step:** Integrate storage into readiness (Task 6)

### 2026-08-30 — Integrate `aks_check_storage` into `aks_validate_upgrade_readiness` (Phase 2 Task 6)
**Phase:** 9
**Task:** Fold storage health into the composite readiness check
**Change:** `aks_validate_upgrade_readiness` now calls `aks_check_storage` inside the existing `check_mode="full"` branch; storage blockers/warnings are extended directly into readiness blockers/warnings; a new `storage_health` key was added to the returned dict (additive, no existing keys changed)
**Files:** `tools/upgrade.py`, `tests/test_upgrade_readiness.py`
**Reason:** POC objective requires PV/PVC/storage problems to factor into upgrade readiness, not just be reported standalone
**Validation:** `get_errors` diagnostics clean; 5 new unit tests written (mocked node/pod/pdb, real storage classification functions via `test_storage.py` fixtures); pytest execution not possible from this workstation (Python not installed)
**Result:** Implemented; safety gates on `aks_upgrade_node_pool` confirmed unchanged by direct file review
**Next step:** Confirm test execution on the VM; implement deprecated API detection tool

### 2026-08-30/31 (VM-side) — Test artifacts committed
**Phase:** 5
**Task:** Local test execution on VM
**Change:** `test_upgrade_readiness.py` committed (commit `9a362ef`); compiled `__pycache__` bytecode for `conftest`, `test_storage`, `test_upgrade_readiness`, and `tools/*` committed (commit `c338690`)
**Files:** (see commits `9a362ef`, `c338690`)
**Reason:** Presumed local validation pass on the VM where Python is installed
**Validation:** Not directly confirmed by us — no captured pytest console output exists in the repo; bytecode presence only proves the modules were imported/collected, not that all assertions passed
**Result:** Unconfirmed — treated as 🟡 IN PROGRESS, not 🟢 DONE, until actual pass/fail output is reviewed
**Next step:** Request/capture the actual pytest output from the VM run

### 2026-08-31 — Create POC-PROGRESS.md
**Phase:** 12
**Task:** Establish a living progress tracker distinct from `README.md`
**Change:** Added this document after inspecting the Foundry agent, both MCP entrypoints, all `tools/*.py` modules, `infra/main.bicep` and its modules, both `azure.yaml` files, `next-steps.md`, `README.md` files, `.gitignore`, and recent git history
**Files:** `POC-PROGRESS.md` (new)
**Reason:** Explicit request for chronological, factual tracking separate from general docs
**Validation:** Cross-checked every status claim against actual source code and git history (no assumptions about untested functionality marked DONE)
**Result:** Document created
**Next step:** See [Section 15](#15-next-steps)

### 2026-08-31 — Implement `aks_check_deprecated_apis` (Phase 5)
**Phase:** 5, 9
**Task:** Detect Kubernetes APIs/resources that are deprecated or removed relative to a target Kubernetes version, and fold findings into upgrade readiness
**Change:** New `tools/deprecated_apis.py` with a 16-entry, explicitly-documented deprecation matrix (group/version/kind, deprecated_in, removed_in, replacement — sourced from the official Kubernetes deprecation guide) and `aks_check_deprecated_apis(subscription_id, resource_group, cluster_name, target_version=None, namespace=None)`. For each matrix entry relevant to the target version, runs a targeted `kubectl get <plural>.<version>.<group>` query (via the existing `run_kubectl_json` helper — same managed-identity/AKS-Run-Command auth as every other tool, no new auth mechanism) to find real objects still using that API, then classifies each as `REMOVED_IN_TARGET` (BLOCKER) or `DEPRECATED_STILL_SERVED` (WARNING) using the project's existing severity vocabulary. If `target_version` is omitted, falls back to the cluster's own current version via `aks_get_cluster_details` (never invents a target). Registered as the 10th tool in both `main.py` and `function_app.py` (identical schemas). `aks_validate_upgrade_readiness` gained a new optional `target_kubernetes_version` parameter and now calls `aks_check_deprecated_apis` in `check_mode="full"`, extending its blockers/warnings exactly like the storage integration (no duplicate classification logic). `aks_upgrade_node_pool` now passes its real `kubernetes_version` through to readiness as the target, so the check is meaningful for actual upgrade attempts without guessing.
**Files:** `tools/deprecated_apis.py` (new), `tools/upgrade.py`, `main.py`, `function_app.py`, `README.md`, `tests/test_deprecated_apis.py` (new), `tests/test_upgrade_readiness.py`
**Reason:** Explicitly required POC objective ("a deprecated API checking tool is still required"); needed to determine target version without inventing one, and to keep detection accurate (usage-based, not just resource-type existence)
**Validation:** `get_errors` diagnostics clean on all 6 changed/new files; 9 new unit tests for the tool (no deprecated APIs / deprecated-still-served / removed-in-target / multiple findings / missing target version fallback / invalid target version / pure classify_entry / determine_deprecated_api_health) plus 1 new readiness-integration test, all using a monkeypatched synthetic matrix and mocked kubectl responses for determinism. **Pytest execution was attempted and failed** — Python is not installed on this workstation (confirmed via Windows Store execution-alias error) — so test results are **not confirmed** from this session. No real AKS cluster was accessed or modified.
**Result:** Implemented; safety gates on `aks_upgrade_node_pool` (dry_run, check_mode, AKS_UPGRADE_ENABLE_WRITE, approval_token) confirmed unchanged by direct file review. No AKS upgrade, resource modification, or cluster creation was performed.
**Next step:** Obtain real pytest console output from the VM for the full suite (23 tests total across `test_storage.py`, `test_upgrade_readiness.py`, `test_deprecated_apis.py`); only then can this be marked 🟢 DONE

## 15. Next Steps

1. Obtain and record actual pytest console output from the VM to confirm all 23 existing unit tests (`test_storage.py` + `test_upgrade_readiness.py` + `test_deprecated_apis.py`) actually pass.
2. Replace the `<AKS_MCP_ENDPOINT>` placeholder in `toolbox.yaml` with a real, deployed Container App URL once one is confirmed to exist and be reachable.
3. Decide on and execute removal/retirement of the legacy Azure Functions hosting path (`function_app.py` + related Bicep resources), or explicitly document why it's being kept.
4. Verify the exact ARM action required for `agent_pools` write operations and add the corresponding RBAC role before any real (non-dry-run) upgrade is attempted.
5. Confirm the Kubernetes RBAC bootstrap scripts have actually been run against the real cluster, and capture that evidence here.
6. Design and run at least one real intentional-failure scenario (start with PV/PVC or PDB, since those have the most existing logic) against the actual AKS cluster, and record the agent's real output.
7. Add unit tests for `tools/discovery.py` and `tools/validation.py` (currently untested).
8. Correlate `aks_get_available_upgrades` output into the `aks_validate_upgrade_readiness` result, so all risk areas are visible in one place.
9. Consider extending the deprecated-API matrix in `tools/deprecated_apis.py` if real-cluster testing surfaces additional in-use deprecated APIs not yet covered.
