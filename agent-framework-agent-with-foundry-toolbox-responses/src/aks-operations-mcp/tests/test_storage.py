"""Unit tests for aks_check_storage classification logic.

These tests exercise only the pure classification/parsing functions in
tools.storage - no Azure credentials or live cluster required.
"""

from __future__ import annotations

from tools.storage import (
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
