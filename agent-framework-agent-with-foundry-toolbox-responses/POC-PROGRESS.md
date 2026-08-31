# AKS Upgrade Agent POC — Progress Tracker

> Living document. Updated whenever a meaningful change is implemented, validated, or troubleshot.
> Status reflects **actual verified state**, not intent. See status legend below.

**Status legend**
- 🟢 DONE — implemented AND validated/tested
- 🟡 IN PROGRESS — implementation exists or work is underway but validation is incomplete
- 🔴 NOT STARTED / MISSING — required work has not been implemented
- ⚠️ BLOCKED — work cannot proceed because of a known blocker

Last updated: 2026-08-31 (aks_check_deprecated_apis optimized: 17 Run Command calls → 1; real-cluster re-validation pending)

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
- **Completed:** 10 tools now implemented and registered identically in both `main.py` and `function_app.py`: `aks_get_cluster_details`, `aks_get_node_pools`, `aks_get_available_upgrades`, `aks_check_node_health`, `aks_check_pod_health`, `aks_check_pdb`, `aks_validate_upgrade_readiness`, `aks_upgrade_node_pool`, `aks_check_storage`, `aks_check_deprecated_apis` (added 2026-08-31). The full unit test suite (22 tests) was executed on the VM on 2026-08-31: `22 passed in 4.12s`. Real-cluster performance diagnosis (2026-08-31) surfaced a severe bottleneck in `aks_check_deprecated_apis` (17 sequential AKS Run Command calls, 546.12s) and `aks_check_pod_health` (cluster-wide output exceeding the 524,288-byte Run Command limit); `aks_check_deprecated_apis` was subsequently redesigned to use a single batched Run Command (implementation complete, unit tests rewritten, real-cluster re-validation not yet performed).
- **Remaining:** No tool has been validated against a real cluster **except** the ad hoc performance-diagnosis timings reported for the pre-optimization implementation. `tools/discovery.py` and `tools/validation.py` (node/pod/pdb) still have no dedicated unit tests. The `aks_check_pod_health` output-size problem is untouched (out of scope for this task). The optimized `aks_check_deprecated_apis` has not yet been unit-tested (pytest not executed this session) or real-cluster-validated.

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
- **Completed:** Unit tests exist for storage classification (8 tests), readiness/storage integration (6 tests), and deprecated-API detection (originally 8 tests; rewritten 2026-08-31 for the batched implementation, see below) — 22 passed on the VM as of 2026-08-31 (pre-optimization code). Real-cluster performance measurements were captured for all 5 non-trivial tools; the dominant bottleneck (`aks_check_deprecated_apis`, 17 Run Command calls / 546.12s) was redesigned into a single-Run-Command implementation.
- **Remaining:** No tests exist for discovery, validation (node/pod/pdb), or the upgrade write path. The rewritten `aks_check_deprecated_apis` tests have **not** been executed in this session (pytest attempt was skipped/unavailable) and the optimized implementation has **not** been re-validated against the real cluster — do not treat the optimization as complete until both happen. `aks_check_pod_health`'s output-size problem (524,288-byte limit exceeded) remains unaddressed.

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

- **Current phase:** Phase 9 (Pre-Upgrade Assessment) is the most advanced; Phase 2/5 (tool completeness + performance) is the current blocker for moving further.
- **Current task:** Re-run the unit test suite and perform real-cluster validation of the optimized `aks_check_deprecated_apis` (1 Run Command call vs. the 17-call/546.12s baseline); only then can this optimization be marked 🟢 DONE.
- **Overall POC status:** All 10 planned MCP tools are implemented. A real-cluster performance diagnosis (2026-08-31) found `aks_check_deprecated_apis` and full readiness (~13 minutes) impractically slow, dominated by AKS Run Command's ~25-35s per-invocation overhead across 17 sequential calls. `aks_check_deprecated_apis` was redesigned to use a single batched Run Command. **This optimization is implemented but NOT YET validated** — neither by an executed pytest run nor by a real-cluster timing comparison. The POC as a whole and real-cluster/end-to-end validation remain incomplete.
- **What is working:** 10 MCP tools implemented; readiness folds in storage + deprecated-API health; prior unit suite (22 tests, pre-optimization) passed on the VM.
- **What is incomplete:** live endpoint wiring (Phase 8), real cluster test scenarios (Phase 10), unit tests for `tools/discovery.py`/`tools/validation.py`, **the just-added optimized deprecated-API implementation is unverified (no pytest run, no real-cluster timing yet)**, and `aks_check_pod_health`'s output-size limit problem is still unresolved.
- **What we are doing next:** Run the full test suite (VM) and the real-cluster validation of `aks_check_deprecated_apis` only (per explicit scope), then report the before/after comparison.

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
| 2026-08-31 | Phase 5: `aks_check_deprecated_apis` | Implemented deprecated/removed Kubernetes API detection tool; integrated into `aks_validate_upgrade_readiness`; registered in both MCP entrypoints | `tools/deprecated_apis.py` (new), `tools/upgrade.py`, `main.py`, `function_app.py`, `README.md`, `tests/test_deprecated_apis.py` (new), `tests/test_upgrade_readiness.py` | `get_errors` diagnostics clean on all changed files; 8 new unit tests written for the tool + 1 new readiness-integration test; **pytest execution not possible from this workstation** (Python not installed — confirmed via Windows Store execution-alias error) | Implemented; validation incomplete at time of writing — see next row for the confirmed VM run |
| 2026-08-31 | Test execution (VM) | Full MCP unit test suite executed on the VM | `tests/test_storage.py`, `tests/test_upgrade_readiness.py`, `tests/test_deprecated_apis.py` (no code changes; test execution only) | **Actual pytest result: `22 passed in 4.12s`** — reported directly by the user after running the suite on the VM | All 22 unit tests pass (mocked/synthetic data only). **Real AKS cluster validation has NOT been performed.** Overall POC and end-to-end/real-cluster validation remain incomplete |
| 2026-08-31 | Real-cluster performance diagnosis | Measured real-cluster elapsed time for 5 tools plus estimated full readiness | No code changes; measurement only | **Reported by user, actual measurements against the real AKS cluster:** `aks_check_node_health` 4.83s; `aks_check_pod_health` 32.12s (cluster-wide output exceeded the 524,288-byte Run Command limit); `aks_check_pdb` 34.58s; `aks_check_storage` 159.14s (namespace-scoped); `aks_check_deprecated_apis` 546.12s (~9.1 min, 17 sequential Run Command calls); full readiness estimated ~13 minutes | Root cause identified: AKS Run Command has ~25-35s of per-invocation overhead; `aks_check_deprecated_apis`'s 17 sequential calls make it the dominant bottleneck. Recorded as the baseline for the optimization below |
| 2026-08-31 | Optimize `aks_check_deprecated_apis` (Run Command batching) | Redesigned the tool to check every relevant matrix entry in a **single** AKS Run Command invocation instead of one call per entry | `tools/common.py` (added `run_kubectl_raw` + internal `_execute_run_command` refactor, `run_kubectl_json` behavior unchanged), `tools/deprecated_apis.py` (rewritten `aks_check_deprecated_apis`, new `_build_batch_script`/`_parse_batch_output`/`_validate_namespace`), `tests/test_deprecated_apis.py` (rewritten: 13 tests) | `get_errors` diagnostics clean on all 3 changed/new files. **Pytest was NOT executed in this session** (attempt was skipped). **Real-cluster validation was NOT performed** — no live timing comparison exists yet for the new implementation | Implemented only. **Not yet validated — do not treat as complete.** Expected call count: 17 → 1 (unverified against the real cluster) |

## 6. Current Work

- Implemented a single-Run-Command redesign of `aks_check_deprecated_apis` in response to the real-cluster performance diagnosis (baseline: 17 calls / 546.12s). **Not yet validated**: pytest has not been executed in this session, and no real-cluster timing comparison exists yet for the new implementation.
- Next: execute the full test suite, then perform real-cluster validation of `aks_check_deprecated_apis` ONLY (per explicit task scope — do not run the full readiness assessment yet) and record the actual call count/elapsed time/output size against the 17-call/546.12s baseline.

## 7. MCP Tool Inventory

| Tool | Purpose | Implementation Status | Validation Status |
|---|---|---|---|
| `aks_get_cluster_details` | Cluster metadata/current version | 🟢 DONE | 🔴 NOT TESTED (no unit tests; no live-cluster run confirmed) |
| `aks_get_node_pools` | Node pool inventory | 🟢 DONE | 🔴 NOT TESTED |
| `aks_get_available_upgrades` | Available K8s/node-image upgrade paths | 🟢 DONE | 🔴 NOT TESTED |
| `aks_check_node_health` | Node Ready/pressure conditions | 🟢 DONE | 🔴 NOT TESTED (only exercised via mocked stubs in readiness tests, not its own logic) |
| `aks_check_pod_health` | Pod phase/restarts/waiting reasons | 🟢 DONE | 🔴 NOT TESTED |
| `aks_check_pdb` | PodDisruptionBudget disruption risk | 🟢 DONE | 🔴 NOT TESTED |
| `aks_validate_upgrade_readiness` | Composite readiness check (node+pod+pdb+storage+maintenance window) | 🟢 DONE (storage now integrated) | � Unit tests passing (VM, `22 passed in 4.12s`, 2026-08-31, includes 6 readiness-integration tests); 🔴 real AKS cluster validation not yet performed |
| `aks_upgrade_node_pool` | Guarded node pool upgrade execution | 🟢 DONE (dry-run default, write gated) | 🔴 NOT TESTED (no unit tests exist for this function) |
| `aks_check_storage` | PVC/PV/StorageClass/pod/event storage health | 🟢 DONE | 🟢 Unit tests passing (VM, `22 passed in 4.12s`, 2026-08-31, includes 8 storage tests); 🔴 real AKS cluster validation not yet performed |
| `aks_check_deprecated_apis` | Detect deprecated/removed Kubernetes API usage vs. a target version | � IN PROGRESS — redesigned 2026-08-31 to use a single batched AKS Run Command (previously 1 call per matrix entry, 17 calls / 546.12s measured against the real cluster — the dominant bottleneck in the whole toolset). Findings now report counts per API version rather than per-object namespace/name (compact-output trade-off, classification accuracy unchanged) | 🔴 NOT YET VALIDATED — unit tests rewritten (13 tests) but pytest has not been executed in this session; real-cluster timing/call-count comparison against the 17-call baseline has NOT been performed |

## 8. AKS Upgrade Risk Coverage

| Risk | Detection Implemented | Tested | Notes |
|---|---|---|---|
| Node health | 🟢 | 🔴 | `aks_check_node_health`; no direct unit test |
| Pod health | 🟢 | 🔴 | `aks_check_pod_health`; no direct unit test |
| PDB | 🟢 | 🔴 | `aks_check_pdb`; no direct unit test |
| PV/PVC | 🟢 | 🟢 | Classification logic unit-tested (`test_storage.py`); confirmed passing on VM (`22 passed in 4.12s`, 2026-08-31); real-cluster validation not yet performed |
| Storage (mount/attach/provisioning failures) | 🟢 | 🟢 | Same as above; also now feeds into readiness (`test_upgrade_readiness.py`); real-cluster validation not yet performed |
| Deprecated/removed Kubernetes APIs | � | 🟡 | `aks_check_deprecated_apis`: redesigned 2026-08-31 to run all relevant matrix-entry checks (17-entry matrix, sourced from the official Kubernetes deprecation guide) in a **single** batched AKS Run Command instead of 17 sequential ones (546.12s baseline on the real cluster). Unit tests rewritten (13 tests) but **not yet executed**; real-cluster re-validation **not yet performed** |
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
| Healthy storage (Bound PVC + Bound PV + Running pod) | `HEALTHY`, no blockers | Unit test asserts this (`test_storage.py::test_healthy_storage`) | � confirmed passing (VM, `22 passed in 4.12s`, 2026-08-31) |
| Pending PVC referenced by an active pod | `BLOCKER` | Unit test asserts this (`test_storage.py::test_pending_pvc_has_clear_reason`) | 🟢 confirmed passing (VM, 2026-08-31) |
| Failed mount / volume attach | Detected as storage failure | Unit tests assert this (`test_failed_mount_is_detected`, `test_failed_volume_attachment_event_is_detected`) | 🟢 confirmed passing (VM, 2026-08-31) |
| Orphaned/unclaimed PV | `WARNING`, never a blocker | Unit test asserts this (`test_orphaned_pv_is_warning_not_blocker`) | 🟢 confirmed passing (VM, 2026-08-31) |
| Unrelated pod failure (ImagePullBackOff) | NOT classified as storage issue | Unit test asserts this (`test_unrelated_pod_failure_is_not_a_storage_problem`) | 🟢 confirmed passing (VM, 2026-08-31) |
| Storage blockers propagate into readiness | `aks_validate_upgrade_readiness` blocked | Unit test asserts this (`test_upgrade_readiness.py::test_pending_pvc_referenced_by_pod_blocks_readiness`, `test_failed_mount_blocks_readiness`) | 🟢 confirmed passing (VM, 2026-08-31) |
| Storage warnings do not block readiness | `is_ready=True`, warning present | Unit test asserts this (`test_orphaned_pv_warns_but_does_not_block_readiness`) | 🟢 confirmed passing (VM, 2026-08-31) |
| No deprecated APIs in use for target version | `HEALTHY`, no findings, 1 Run Command call | Unit test asserts this (`test_deprecated_apis.py::test_no_deprecated_apis_detected`) | 🟡 rewritten, not yet executed |
| Deprecated-but-still-served API detected | `WARNING`, not blocked, count reported | Unit test asserts this (`test_deprecated_api_still_served_is_a_warning`) | 🟡 rewritten, not yet executed |
| Removed-in-target API detected | `BLOCKER`, count reported | Unit test asserts this (`test_removed_api_in_target_is_a_blocker`) | 🟡 rewritten, not yet executed |
| Multiple deprecated/removed API findings (single batched call) | All reported together, `run_command_invocations == 1` | Unit test asserts this (`test_multiple_findings_across_api_versions`) | 🟡 rewritten, not yet executed |
| API unavailable (non-zero kubectl exit) vs. zero objects found | Unavailable API surfaces as a query error, never silently reported as "healthy"/no findings | Unit test asserts this (`test_api_unavailable_is_distinguished_from_no_objects_found`) | 🟡 new, not yet executed |
| Missing target version | Falls back to cluster's current version, no error | Unit test asserts this (`test_missing_target_version_falls_back_to_cluster_current_version`) | 🟡 rewritten, not yet executed |
| Invalid target version format | Raises `ValueError` | Unit test asserts this (`test_invalid_target_version_raises`) | 🟡 rewritten, not yet executed |
| Compact/batched response parsing | `{index: (exit_code, count)}` extracted correctly, incl. with surrounding banner noise | Unit tests assert this (`test_parse_batch_output_extracts_multiple_entries`, `test_parse_batch_output_ignores_surrounding_noise`) | 🟡 new, not yet executed |
| Batch script shape (single invocation, compact output only) | One script covers all relevant entries; requests `-o name`, never `-o json` | Unit test asserts this (`test_build_batch_script_uses_single_invocation_shape`) | 🟡 new, not yet executed |
| Invalid namespace rejected (injection hardening) | Raises `ValueError` for a non-RFC-1123 namespace value | Unit test asserts this (`test_invalid_namespace_raises`) | 🟡 new, not yet executed |
| Deprecated API findings propagate into readiness | Readiness blocked when a removed-API finding exists | Unit test asserts this (`test_upgrade_readiness.py::test_deprecated_api_findings_are_included_in_readiness`) | 🟢 confirmed passing (VM, 2026-08-31) — this test monkeypatches the whole function, so it is unaffected by the internal batching rewrite |
| PDB disruption/eviction constraint (real cluster) | Detected as blocker | Not attempted | 🔴 NOT STARTED |
| Deprecated API usage (real cluster) | Detected and explained | Not attempted since the rewrite; prior implementation's real-cluster run only measured timing (546.12s / 17 calls), not correctness | 🔴 NOT STARTED |
| Unhealthy pod (real cluster) | Detected as blocker | Not attempted | 🔴 NOT STARTED |
| Node health problem (real cluster) | Detected as blocker | Not attempted | 🔴 NOT STARTED |

> Note: on 2026-08-31 the user ran the full MCP unit test suite (pre-optimization code) on the VM and reported `22 passed in 4.12s`. The `aks_check_deprecated_apis` tests were then rewritten for the new batched implementation and have **not** been re-executed. Do not read the rows above marked "rewritten/new, not yet executed" as passing until a real pytest run confirms it.

## 12. Problems / Blockers

| Date | Problem | Impact | Investigation | Resolution | Current Status |
|---|---|---|---|---|---|
| 2026-08-30 | `configure-aks-mcp-container-identity.ps1` used `az containerapp update --set-env-vars`, which replaces the entire env var list | Could silently wipe unrelated Container App configuration | Confirmed via known `az` CLI behavior | Rewrote script to fetch existing env vars, merge in the 4 managed vars by key, and pass the full merged list back | 🟢 Fixed (not live-validated against a real container app) |
| 2026-08-30 | Write-path RBAC action for `agent_pools` upgrades not confidently verified | Real (non-dry-run) node pool upgrades will fail authorization | Checked built-in role catalog; no authoritative source found for the exact write action in this session | Deliberately left unresolved rather than guessing, per explicit instruction | ⚠️ BLOCKED — needs an authoritative source before granting write RBAC |
| Ongoing | Python is not installed on this work PC | Cannot execute `pytest` or validate runtime behavior from this workstation | Confirmed via Windows Store execution-alias error when invoking `python` | User runs tests on a separate VM | ⚠️ BLOCKED (workstation-specific; not a code issue) |
| 2026-08-30 | Two parallel MCP hosting paths (`main.py` for Container Apps, `function_app.py` for Azure Functions) exist with historically different tool sets | Confusion about which is authoritative; doubles maintenance surface | Confirmed both now expose identical 9 tools, but legacy Function infra still provisions by default | Not yet resolved — pending decision to retire the Function App path | 🟡 Open |
| 2026-08-30 | `toolbox.yaml`'s `aks-mcp` entry has a literal placeholder `server_url` | Foundry agent cannot actually reach the MCP server until this is set | Confirmed via direct file read | Not yet resolved | 🔴 Open |
| Repo hygiene | `updates from vm` commit added compiled `__pycache__` bytecode files to source control | Repo noise; `.gitignore` only excludes `.azure` | Confirmed via `git show --stat` | Not yet fixed | 🟡 Open (minor, no functional impact) |
| 2026-08-31 | Real-cluster performance diagnosis found `aks_check_deprecated_apis` took 546.12s (17 sequential AKS Run Command calls), `aks_check_pod_health` took 32.12s and exceeded the 524,288-byte Run Command output limit, and full readiness would take ~13 minutes | POC is impractically slow for interactive use; `aks_check_pod_health` risks truncated/corrupt output on cluster-wide queries | Root cause: AKS Run Command has ~25-35s of per-invocation overhead; `aks_check_deprecated_apis`'s 17 sequential calls made it the dominant cost | `aks_check_deprecated_apis` redesigned to use 1 batched Run Command instead of 17 (this task). `aks_check_pod_health`'s output-size problem was explicitly OUT OF SCOPE for this task and remains unresolved | 🟡 Partially addressed — `aks_check_deprecated_apis` fix implemented but **not yet validated** (no pytest run, no real-cluster re-measurement); `aks_check_pod_health` issue still ⚠️ BLOCKED/open |

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
- **Deprecated-API detection uses targeted `kubectl get <plural>.<version>.<group>` queries against a small, explicitly-maintained matrix** (17 entries, sourced from the official Kubernetes deprecation guide) rather than an exhaustive historical compatibility database, and rather than trusting `kubectl api-resources` alone (which only reflects what the *current* server serves, not what will be removed by a future target version).
- **When `target_version` is not supplied to `aks_check_deprecated_apis`, it falls back to the cluster's own current `kubernetes_version`** (via `aks_get_cluster_details`) rather than inventing or guessing an upgrade target. When invoked internally from `aks_upgrade_node_pool` (via `aks_validate_upgrade_readiness`), the real requested `kubernetes_version` is passed through automatically, so the check is meaningful for that specific upgrade attempt without guessing.
- **`aks_check_deprecated_apis` was redesigned to issue ONE AKS Run Command invocation instead of 17** (2026-08-31), after a real-cluster performance diagnosis measured 546.12s / 17 sequential calls, driven by AKS Run Command's ~25-35s per-invocation overhead. Parallelizing the 17 calls was explicitly rejected as the primary fix (per instruction) in favor of batching them into a single remote script.
- **The batched script reports only compact counts per API version (`-o name` + count), never full object JSON** — this keeps output far below AKS Run Command's 524,288-byte limit and avoids the same truncation risk seen in `aks_check_pod_health`. The trade-off is losing per-object namespace/name detail in findings (only a count remains); classification accuracy (BLOCKER/WARNING) is unaffected.
- **Each batched entry's kubectl exit code is captured separately from its output** (via command substitution, not a plain pipe) specifically so an unavailable/removed API (non-zero exit) can never be misread as "zero objects found" (exit 0). This was a deliberate, tested distinction, not an assumption.
- **Added namespace input validation (`_validate_namespace`, RFC 1123 label format) to `aks_check_deprecated_apis`** as part of this change — the `namespace` parameter is interpolated into a shell command executed via AKS Run Command, and the new batched script is more complex than the previous single-`kubectl` invocation, so this closes a latent shell-injection risk for this tool specifically (identical interpolation exists unmodified in other tools' `-n {namespace}` usage, which was out of scope for this task).

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
**Change:** New `tools/deprecated_apis.py` with a 17-entry, explicitly-documented deprecation matrix (group/version/kind, deprecated_in, removed_in, replacement — sourced from the official Kubernetes deprecation guide) and `aks_check_deprecated_apis(subscription_id, resource_group, cluster_name, target_version=None, namespace=None)`. For each matrix entry relevant to the target version, runs a targeted `kubectl get <plural>.<version>.<group>` query (via the existing `run_kubectl_json` helper — same managed-identity/AKS-Run-Command auth as every other tool, no new auth mechanism) to find real objects still using that API, then classifies each as `REMOVED_IN_TARGET` (BLOCKER) or `DEPRECATED_STILL_SERVED` (WARNING) using the project's existing severity vocabulary. If `target_version` is omitted, falls back to the cluster's own current version via `aks_get_cluster_details` (never invents a target). Registered as the 10th tool in both `main.py` and `function_app.py` (identical schemas). `aks_validate_upgrade_readiness` gained a new optional `target_kubernetes_version` parameter and now calls `aks_check_deprecated_apis` in `check_mode="full"`, extending its blockers/warnings exactly like the storage integration (no duplicate classification logic). `aks_upgrade_node_pool` now passes its real `kubernetes_version` through to readiness as the target, so the check is meaningful for actual upgrade attempts without guessing.
**Files:** `tools/deprecated_apis.py` (new), `tools/upgrade.py`, `main.py`, `function_app.py`, `README.md`, `tests/test_deprecated_apis.py` (new), `tests/test_upgrade_readiness.py`
**Reason:** Explicitly required POC objective ("a deprecated API checking tool is still required"); needed to determine target version without inventing one, and to keep detection accurate (usage-based, not just resource-type existence)
**Validation:** `get_errors` diagnostics clean on all 6 changed/new files; 8 new unit tests for the tool (no deprecated APIs / deprecated-still-served / removed-in-target / multiple findings / missing target version fallback / invalid target version / pure classify_entry / determine_deprecated_api_health) plus 1 new readiness-integration test, all using a monkeypatched synthetic matrix and mocked kubectl responses for determinism. **Pytest execution was attempted and failed** from this workstation — Python is not installed here (confirmed via Windows Store execution-alias error). No real AKS cluster was accessed or modified.
**Result:** Implemented; safety gates on `aks_upgrade_node_pool` (dry_run, check_mode, AKS_UPGRADE_ENABLE_WRITE, approval_token) confirmed unchanged by direct file review. No AKS upgrade, resource modification, or cluster creation was performed.
**Next step:** Obtain real pytest console output from the VM for the full suite (22 tests total across `test_storage.py`, `test_upgrade_readiness.py`, `test_deprecated_apis.py`) — see the following entry, where this was completed

### 2026-08-31 — VM pytest run confirmed: `22 passed in 4.12s`
**Phase:** 5, 9
**Task:** Execute the full MCP unit test suite on the VM (where Python is installed) and record the actual result
**Change:** No application code changes. The user ran the full test suite (`test_storage.py` + `test_upgrade_readiness.py` + `test_deprecated_apis.py`, 22 tests total) on the VM.
**Files:** None changed (test execution only); this update only touches `POC-PROGRESS.md`
**Reason:** Convert previously-"unconfirmed" unit test claims into a verified, recorded result, per the project's rule against claiming DONE without real validation
**Validation:** Actual pytest output reported: `22 passed in 4.12s`. This matches the true count of `def test_` functions across the three test files (8 in `test_storage.py` + 6 in `test_upgrade_readiness.py` + 8 in `test_deprecated_apis.py` = 22) — corrects an earlier miscount in this document that referred to "9" deprecated-API tests and "23" tests total.
**Result:** All 22 unit tests pass against mocked/synthetic data. This validates the classification and integration logic in isolation. **It does NOT constitute real AKS cluster validation** — no live cluster, Foundry agent, or deployed MCP endpoint was exercised. The overall POC and Phases 10/11 (intentional failure testing, end-to-end validation) remain incomplete.
**Next step:** Proceed to Phase 8 (real MCP endpoint wiring) and plan real-AKS-cluster validation (Phase 10/11)

### 2026-08-31 — Optimize `aks_check_deprecated_apis`: 17 Run Command calls → 1 (Phase 2/5)
**Phase:** 2, 5
**Task:** Fix the dominant performance bottleneck identified by a real-cluster diagnosis, without parallelizing the 17 calls
**Change:** Real-cluster measurements were reported: `aks_check_node_health` 4.83s, `aks_check_pod_health` 32.12s (output exceeded the 524,288-byte Run Command limit), `aks_check_pdb` 34.58s, `aks_check_storage` 159.14s (namespace-scoped), `aks_check_deprecated_apis` 546.12s / 17 sequential Run Command calls, full readiness ~13 minutes. Root cause: AKS Run Command's ~25-35s per-invocation overhead. `tools/common.py` gained a new `run_kubectl_raw` helper (via an internal `_execute_run_command` refactor that leaves `run_kubectl_json`'s behavior byte-for-byte unchanged). `tools/deprecated_apis.py`'s `aks_check_deprecated_apis` was rewritten to build one shell script (`_build_batch_script`) covering every relevant matrix entry, submit it as a SINGLE `run_kubectl_raw` call, and parse the compact response (`_parse_batch_output`) back into per-entry `(exit_code, count)` pairs. Each entry's kubectl exit code is captured via command substitution (never through a pipe, which would lose it) so an unavailable/removed API is never confused with zero objects found. Only compact `-o name` + count output is requested per entry - never full object JSON. Findings now report a `count` per API version instead of per-object namespace/name (disclosed trade-off; classification accuracy unchanged). Added `_validate_namespace` (RFC 1123 format check) since the batched script is more complex than the previous single-kubectl-call design. `classify_entry`, `determine_deprecated_api_health`, the 17-entry matrix, and target-version resolution (including the `aks_upgrade_node_pool` → `aks_validate_upgrade_readiness` pass-through) are all unchanged. The public function signature and MCP tool registration are unchanged.
**Files:** `tools/common.py`, `tools/deprecated_apis.py`, `tests/test_deprecated_apis.py` (rewritten: 13 tests)
**Reason:** Explicit task to fix the measured bottleneck via batching (not parallelization), while preserving classification behavior, target-version resolution, and the MCP interface
**Validation:** `get_errors` diagnostics clean on all 3 changed files. **Pytest was NOT executed in this session** (the attempt was skipped/unavailable in this environment). **Real-cluster validation was NOT performed** — no live AKS cluster, Foundry agent, or Container App was touched, and no before/after timing comparison exists yet. No AKS cluster, PDB, PVC, node pool, upgrade configuration, Container App, or Foundry integration was modified, per the explicit safety constraints.
**Result:** Implementation complete; **status is 🟡 IN PROGRESS, not 🟢 DONE**, per the explicit instruction not to mark this optimization complete without real validation. Expected outcome (unverified): 17 calls → 1 call; 546.12s → a small multiple of the ~25-35s per-invocation overhead instead of 17× it.
**Next step:** Execute the full pytest suite, then perform real-cluster validation of `aks_check_deprecated_apis` ONLY (per explicit scope) and record actual call count, elapsed time, result, and output size against the 17-call/546.12s baseline

## 15. Next Steps

1. **Execute the full pytest suite** (VM) covering the rewritten `test_deprecated_apis.py` (13 tests) alongside the existing storage/readiness tests, and record the actual result.
2. **Perform real-cluster validation of `aks_check_deprecated_apis` ONLY** (per explicit scope — do not run the full readiness assessment yet): measure number of Run Command invocations, elapsed time, result correctness, and confirm output stays below the 524,288-byte limit. Compare against the `17 calls / 546.12s` baseline.
3. Address `aks_check_pod_health`'s output-size problem (cluster-wide query exceeded the 524,288-byte Run Command limit) — out of scope for this task, still open.
4. Replace the `<AKS_MCP_ENDPOINT>` placeholder in `toolbox.yaml` with a real, deployed Container App URL once one is confirmed to exist and be reachable.
5. Decide on and execute removal/retirement of the legacy Azure Functions hosting path (`function_app.py` + related Bicep resources), or explicitly document why it's being kept.
6. Verify the exact ARM action required for `agent_pools` write operations and add the corresponding RBAC role before any real (non-dry-run) upgrade is attempted.
7. Confirm the Kubernetes RBAC bootstrap scripts have actually been run against the real cluster, and capture that evidence here.
8. Design and run at least one real intentional-failure scenario (start with PV/PVC or PDB, since those have the most existing logic) against the actual AKS cluster, and record the agent's real output.
9. Add unit tests for `tools/discovery.py` and `tools/validation.py` (currently untested).
10. Correlate `aks_get_available_upgrades` output into the `aks_validate_upgrade_readiness` result, so all risk areas are visible in one place.
11. Once `aks_check_deprecated_apis` is re-validated, re-measure full readiness (`check_mode="full"`) end-to-end — previously estimated at ~13 minutes, now expected to drop substantially given the 17→1 Run Command reduction (unverified until measured).
