"""Unit tests for aks_check_storage classification logic.

These tests exercise only the pure classification/parsing functions in
tools.storage - no Azure credentials or live cluster required.
"""

from __future__ import annotations

import json

import pytest

from tools import storage
from tools.storage import (
    _extract_items,
    _validate_namespace,
    aks_check_storage,
    classify_pvcs,
    classify_pvs,
    determine_storage_health,
    find_pod_storage_failures,
    find_storage_events,
    summarize_storage_classes,
)


def _pvc(namespace: str, name: str, phase: str, storage_class_name: str | None = None, volume_name: str | None = None) -> dict:
    return {
        "metadata": {"namespace": namespace, "name": name},
        "spec": {"storageClassName": storage_class_name, "volumeName": volume_name},
        "status": {"phase": phase},
    }


def _pv(name: str, phase: str, claim_ref: dict | None = None) -> dict:
    return {
        "metadata": {"name": name},
        "spec": {"claimRef": claim_ref},
        "status": {"phase": phase},
    }


def _pod(namespace: str, name: str, phase: str, waiting_reason: str | None = None, waiting_message: str | None = None, pvc_claim: str | None = None) -> dict:
    container_status = {"state": {}}
    if waiting_reason:
        container_status["state"]["waiting"] = {"reason": waiting_reason, "message": waiting_message}
    volumes = []
    if pvc_claim:
        volumes.append({"persistentVolumeClaim": {"claimName": pvc_claim}})
    return {
        "metadata": {"namespace": namespace, "name": name},
        "spec": {"volumes": volumes},
        "status": {"phase": phase, "containerStatuses": [container_status] if waiting_reason else []},
    }


def _event(namespace: str, reason: str, message: str, kind: str = "Pod", involved_name: str = "some-pod") -> dict:
    return {
        "metadata": {"namespace": namespace},
        "reason": reason,
        "message": message,
        "involvedObject": {"kind": kind, "name": involved_name, "namespace": namespace},
        "lastTimestamp": "2026-08-30T00:00:00Z",
    }


def test_healthy_storage():
    """Test 1: Bound PVC + Bound PV + healthy pod -> HEALTHY."""
    pvcs = [_pvc("payments", "db-data", "Bound", volume_name="pv-1")]
    pvs = [_pv("pv-1", "Bound", claim_ref={"namespace": "payments", "name": "db-data"})]
    pods = [_pod("payments", "db-pod", "Running")]

    pvc_result = classify_pvcs(pvcs, pods)
    pv_result = classify_pvs(pvs)
    pod_failures = find_pod_storage_failures(pods)

    assert pvc_result["problematic"] == []
    assert pv_result["unbound_or_problematic"] == []
    assert pod_failures == []

    blockers = []
    warnings = []
    status = determine_storage_health(blockers, warnings)
    assert status == "HEALTHY"


def test_pending_pvc_has_clear_reason():
    """Test 2: Pending PVC -> WARNING or BLOCKED, always with a clear reason."""
    # Not referenced by any pod -> WARNING (not a blocker without context).
    pvcs = [_pvc("billing", "invoices", "Pending")]
    result = classify_pvcs(pvcs, pod_items=[])
    assert result["pending"] == 1
    entry = result["problematic"][0]
    assert entry["severity"] in ("WARNING", "BLOCKER")
    assert entry["reason"]  # non-empty explanation required

    # Referenced by an active pod -> BLOCKER, still with a clear reason.
    pods = [_pod("billing", "invoices-app", "Pending", pvc_claim="invoices")]
    result_referenced = classify_pvcs(pvcs, pods)
    entry_referenced = result_referenced["problematic"][0]
    assert entry_referenced["severity"] == "BLOCKER"
    assert entry_referenced["reason"]


def test_failed_mount_is_detected():
    """Test 3: Pod/container has a storage-related failure -> detected."""
    pods = [
        _pod(
            "payments",
            "db-pod",
            "Pending",
            waiting_reason="FailedMount",
            waiting_message="Unable to mount volumes for pod",
        )
    ]

    failures = find_pod_storage_failures(pods)

    assert len(failures) == 1
    assert failures[0]["namespace"] == "payments"
    assert failures[0]["name"] == "db-pod"


def test_failed_volume_attachment_event_is_detected():
    """Test 4: Storage-related event indicates attach failure -> detected."""
    events = [
        _event(
            "payments",
            reason="FailedAttachVolume",
            message="AttachVolume.Attach failed for volume pv-1",
            involved_name="db-pod",
        )
    ]

    storage_events = find_storage_events(events)

    assert len(storage_events) == 1
    assert storage_events[0]["reason"] == "FailedAttachVolume"


def test_unrelated_pod_failure_is_not_a_storage_problem():
    """Test 5: Image-pull failure is unrelated to storage -> NOT classified as one."""
    pods = [
        _pod(
            "web",
            "frontend-pod",
            "Pending",
            waiting_reason="ImagePullBackOff",
            waiting_message="Back-off pulling image \"myapp:latest\"",
        )
    ]

    failures = find_pod_storage_failures(pods)

    assert failures == []


def test_orphaned_pv_is_warning_not_blocker():
    """Test 6: PV not Bound but with no active workload dependency -> WARNING, not BLOCKED."""
    pvs = [_pv("old-pv", "Available")]

    result = classify_pvs(pvs)

    assert len(result["unbound_or_problematic"]) == 1
    assert result["unbound_or_problematic"][0]["severity"] == "WARNING"


def test_multiple_namespaces_reported_correctly():
    """Test 7: PVCs from multiple namespaces are reported with correct namespace/name."""
    pvcs = [
        _pvc("payments", "db-data", "Pending"),
        _pvc("billing", "invoices", "Pending"),
    ]

    result = classify_pvcs(pvcs, pod_items=[])

    identifiers = {(item["namespace"], item["name"]) for item in result["problematic"]}
    assert ("payments", "db-data") in identifiers
    assert ("billing", "invoices") in identifiers
    assert result["total"] == 2


def test_storage_class_summary_is_informational_only():
    sc_items = [
        {
            "metadata": {"name": "managed-premium"},
            "provisioner": "disk.csi.azure.com",
            "reclaimPolicy": "Delete",
            "volumeBindingMode": "WaitForFirstConsumer",
            "allowVolumeExpansion": True,
        }
    ]

    summary, by_name = summarize_storage_classes(sc_items)

    assert summary["total"] == 1
    assert by_name["managed-premium"]["volume_binding_mode"] == "WaitForFirstConsumer"


# --- Batching/parser tests for the single-Run-Command aks_check_storage (2026-08-31) ---


def _items_json(items: list[dict]) -> str:
    return json.dumps({"items": items})


def _healthy_batch() -> dict[str, tuple[int, str]]:
    empty = _items_json([])
    return {"pvc": (0, empty), "pv": (0, empty), "storageclass": (0, empty), "pods": (0, empty), "events": (0, empty)}


def test_extract_items_parses_successful_query():
    batch = {"pvc": (0, _items_json([{"metadata": {"name": "a"}}]))}
    errors: list[str] = []

    items = _extract_items("pvc", batch, errors)

    assert items == [{"metadata": {"name": "a"}}]
    assert errors == []


def test_extract_items_nonzero_exit_is_a_query_error_not_an_empty_result():
    """A failed query must be preserved as an explicit error, never silently treated as empty."""
    batch = {"pvc": (1, "")}
    errors: list[str] = []

    items = _extract_items("pvc", batch, errors)

    assert items == []
    assert len(errors) == 1
    assert "kubectl exited with code 1" in errors[0]


def test_extract_items_missing_label_is_a_query_error():
    errors: list[str] = []

    items = _extract_items("pvc", {}, errors)

    assert items == []
    assert "no result returned" in errors[0]


def test_extract_items_malformed_json_is_a_query_error():
    batch = {"pvc": (0, "{not valid json")}
    errors: list[str] = []

    items = _extract_items("pvc", batch, errors)

    assert items == []
    assert "failed to parse kubectl JSON output" in errors[0]


def test_invalid_namespace_raises():
    with pytest.raises(ValueError):
        _validate_namespace("not; a valid namespace")


def test_aks_check_storage_uses_a_single_batched_run_command(monkeypatch):
    """All 5 queries must be issued via exactly one run_kubectl_batch call."""
    call_count = {"n": 0}

    def fake_batch(subscription_id, resource_group, cluster_name, queries):
        call_count["n"] += 1
        assert set(queries.keys()) == {"pvc", "pv", "storageclass", "pods", "events"}
        return _healthy_batch()

    monkeypatch.setattr(storage, "run_kubectl_batch", fake_batch)

    result = aks_check_storage("sub", "rg", "cluster")

    assert call_count["n"] == 1
    assert result["run_command_invocations"] == 1
    assert result["storage_health"] == "HEALTHY"
    assert result["query_errors"] == []


def test_aks_check_storage_query_failure_is_not_hidden_as_healthy(monkeypatch):
    """A failed PVC query must surface as a query_error and must NOT produce a false
    'No storage issues detected' recommendation."""
    batch = _healthy_batch()
    batch["pvc"] = (1, "")
    monkeypatch.setattr(storage, "run_kubectl_batch", lambda *a, **k: batch)

    result = aks_check_storage("sub", "rg", "cluster")

    assert result["blockers"] == []
    assert result["warnings"] == []
    assert len(result["query_errors"]) == 1
    assert "pvc" in result["query_errors"][0]
    assert not any("No storage issues detected" in r for r in result["recommendations"])
    assert any("could not be executed" in r for r in result["recommendations"])


def test_aks_check_storage_events_failure_stays_best_effort(monkeypatch):
    """Events failing must not crash the tool and must still populate events_available/error,
    while also being visible in the unified query_errors list."""
    batch = _healthy_batch()
    batch["events"] = (1, "")
    monkeypatch.setattr(storage, "run_kubectl_batch", lambda *a, **k: batch)

    result = aks_check_storage("sub", "rg", "cluster")

    assert result["storage_events"]["events_available"] is False
    assert result["storage_events"]["error"] is not None
    assert len(result["query_errors"]) == 1
    assert "events" in result["query_errors"][0]
