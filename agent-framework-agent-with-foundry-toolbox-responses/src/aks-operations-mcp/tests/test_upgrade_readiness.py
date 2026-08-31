"""Unit tests for storage health integration into aks_validate_upgrade_readiness.

Node/pod/pdb checks are stubbed as healthy so each test isolates the effect of
storage findings on the overall readiness result. Storage findings themselves are
produced via the real tools.storage classification functions (reusing test_storage's
fixture helpers) so this test never re-implements storage detection logic.
"""

from __future__ import annotations

from typing import Any

from test_storage import _pod, _pv, _pvc
from tools.storage import classify_pvcs, classify_pvs, determine_storage_health, find_pod_storage_failures
from tools.upgrade import aks_validate_upgrade_readiness


def _healthy_node_health(*_args: Any, **_kwargs: Any) -> dict:
    return {"cluster_name": "c", "total_nodes": 1, "healthy_nodes": 1, "unhealthy_nodes": []}


def _healthy_pod_health(*_args: Any, **_kwargs: Any) -> dict:
    return {"cluster_name": "c", "scope": "all-namespaces", "total_pods": 1, "healthy_pods": 1, "unhealthy_pods": []}


def _healthy_pdb_health(*_args: Any, **_kwargs: Any) -> dict:
    return {"cluster_name": "c", "scope": "all-namespaces", "total_pdbs": 0, "blocking_pdbs": [], "is_upgrade_safe": True}


def _healthy_deprecated_api_health(*_args: Any, **_kwargs: Any) -> dict:
    return {
        "deprecated_api_health": "HEALTHY",
        "target_kubernetes_version": "1.28.0",
        "findings": [],
        "blockers": [],
        "warnings": [],
    }


def _storage_health_from(pvc_items=(), pv_items=(), pod_items=()) -> dict:
    """Build a storage_health payload the same way aks_check_storage does, without kubectl I/O."""
    pod_storage_failures = find_pod_storage_failures(list(pod_items))
    pvcs = classify_pvcs(list(pvc_items), list(pod_items))
    pvs = classify_pvs(list(pv_items))

    blockers: list[str] = []
    warnings: list[str] = []

    for item in pvcs["problematic"]:
        (blockers if item["severity"] == "BLOCKER" else warnings).append(
            f"PVC {item['namespace']}/{item['name']}: {item['reason']}"
        )
    for item in pvs["unbound_or_problematic"]:
        (blockers if item["severity"] == "BLOCKER" else warnings).append(f"PV {item['name']}: {item['reason']}")
    for failure in pod_storage_failures:
        blockers.append(
            f"Pod {failure['namespace']}/{failure['name']}: storage-related failure ({failure.get('reason')})."
        )

    return {
        "storage_health": determine_storage_health(blockers, warnings),
        "pvcs": pvcs,
        "pvs": pvs,
        "pod_storage_failures": {"count": len(pod_storage_failures), "pods": pod_storage_failures},
        "blockers": blockers,
        "warnings": warnings,
    }


def _patch_healthy_baseline(monkeypatch) -> None:
    monkeypatch.setattr("tools.upgrade.aks_check_node_health", _healthy_node_health)
    monkeypatch.setattr("tools.upgrade.aks_check_pod_health", _healthy_pod_health)
    monkeypatch.setattr("tools.upgrade.aks_check_pdb", _healthy_pdb_health)
    monkeypatch.setattr("tools.upgrade.aks_check_deprecated_apis", _healthy_deprecated_api_health)


def _patch_storage(monkeypatch, storage_health: dict) -> None:
    monkeypatch.setattr("tools.upgrade.aks_check_storage", lambda *a, **k: storage_health)


def _patch_deprecated_apis(monkeypatch, deprecated_api_health: dict) -> None:
    monkeypatch.setattr("tools.upgrade.aks_check_deprecated_apis", lambda *a, **k: deprecated_api_health)


def test_healthy_storage_does_not_block_readiness(monkeypatch):
    _patch_healthy_baseline(monkeypatch)
    pvcs = [_pvc("payments", "db-data", "Bound", volume_name="pv-1")]
    pvs = [_pv("pv-1", "Bound", claim_ref={"namespace": "payments", "name": "db-data"})]
    pods = [_pod("payments", "db-pod", "Running")]
    _patch_storage(monkeypatch, _storage_health_from(pvcs, pvs, pods))

    result = aks_validate_upgrade_readiness("sub", "rg", "cluster", check_mode="full")

    assert result["storage_health"]["storage_health"] == "HEALTHY"
    assert result["readiness"]["is_ready"] is True
    assert result["readiness"]["blockers"] == []


def test_pending_pvc_referenced_by_pod_blocks_readiness(monkeypatch):
    _patch_healthy_baseline(monkeypatch)
    pvcs = [_pvc("billing", "invoices", "Pending")]
    pods = [_pod("billing", "invoices-app", "Pending", pvc_claim="invoices")]
    _patch_storage(monkeypatch, _storage_health_from(pvcs, pod_items=pods))

    result = aks_validate_upgrade_readiness("sub", "rg", "cluster", check_mode="full")

    assert result["readiness"]["is_ready"] is False
    assert any("invoices" in blocker for blocker in result["readiness"]["blockers"])


def test_failed_mount_blocks_readiness(monkeypatch):
    _patch_healthy_baseline(monkeypatch)
    pods = [
        _pod(
            "payments",
            "db-pod",
            "Pending",
            waiting_reason="FailedMount",
            waiting_message="Unable to mount volumes for pod",
        )
    ]
    _patch_storage(monkeypatch, _storage_health_from(pod_items=pods))

    result = aks_validate_upgrade_readiness("sub", "rg", "cluster", check_mode="full")

    assert result["readiness"]["is_ready"] is False
    assert any("db-pod" in blocker and "FailedMount" in blocker for blocker in result["readiness"]["blockers"])


def test_orphaned_pv_warns_but_does_not_block_readiness(monkeypatch):
    _patch_healthy_baseline(monkeypatch)
    pvs = [_pv("old-pv", "Available")]
    _patch_storage(monkeypatch, _storage_health_from(pv_items=pvs))

    result = aks_validate_upgrade_readiness("sub", "rg", "cluster", check_mode="full")

    assert result["readiness"]["is_ready"] is True
    assert result["readiness"]["blockers"] == []
    assert any("old-pv" in warning for warning in result["readiness"]["warnings"])


def test_unrelated_pod_failure_is_not_a_storage_blocker(monkeypatch):
    _patch_healthy_baseline(monkeypatch)
    pods = [
        _pod(
            "web",
            "frontend-pod",
            "Pending",
            waiting_reason="ImagePullBackOff",
            waiting_message="Back-off pulling image \"myapp:latest\"",
        )
    ]
    _patch_storage(monkeypatch, _storage_health_from(pod_items=pods))

    result = aks_validate_upgrade_readiness("sub", "rg", "cluster", check_mode="full")

    assert result["storage_health"]["blockers"] == []
    assert result["readiness"]["is_ready"] is True


def test_deprecated_api_findings_are_included_in_readiness(monkeypatch):
    """Test 6: a removed-API-in-target finding from aks_check_deprecated_apis blocks readiness,
    while a deprecated-but-still-served finding only warns (mirrors the storage severity model)."""
    _patch_healthy_baseline(monkeypatch)
    _patch_deprecated_apis(
        monkeypatch,
        {
            "deprecated_api_health": "BLOCKED",
            "target_kubernetes_version": "1.25.0",
            "findings": [
                {
                    "namespace": "payments",
                    "name": "db-pdb",
                    "kind": "PodDisruptionBudget",
                    "api_version": "policy/v1beta1",
                    "severity": "BLOCKER",
                    "status": "REMOVED_IN_TARGET",
                }
            ],
            "blockers": ["PodDisruptionBudget payments/db-pdb (policy/v1beta1): removed in target version 1.25.0."],
            "warnings": [],
        },
    )

    result = aks_validate_upgrade_readiness(
        "sub", "rg", "cluster", check_mode="full", target_kubernetes_version="1.25.0"
    )

    assert result["readiness"]["is_ready"] is False
    assert any("db-pdb" in blocker for blocker in result["readiness"]["blockers"])
    assert result["deprecated_api_health"]["deprecated_api_health"] == "BLOCKED"
