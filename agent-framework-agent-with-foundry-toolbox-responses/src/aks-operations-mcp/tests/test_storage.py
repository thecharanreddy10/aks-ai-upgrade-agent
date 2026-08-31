"""Unit tests for aks_check_storage classification logic.

These tests exercise only the pure classification/parsing functions in
tools.storage - no Azure credentials or live cluster required.
"""

from __future__ import annotations

import json

import pytest

from tools import storage
from tools.storage import (
    _extract_event_storage_rows,
    _extract_items,
    _extract_pod_storage_rows,
    _parse_event_storage_rows,
    _parse_pod_storage_rows,
    _parse_storage_batch_output,
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


# --- determine_storage_health precedence tests (2026-08-31 correctness fix) ---


def test_determine_storage_health_healthy_when_nothing_present():
    assert determine_storage_health([], [], []) == "HEALTHY"
    assert determine_storage_health([], []) == "HEALTHY"  # query_errors defaults to None


def test_determine_storage_health_query_errors_only_is_incomplete():
    """A query failure with no confirmed findings must be INCOMPLETE, never HEALTHY."""
    assert determine_storage_health([], [], ["pods: no result returned in the batched output."]) == "INCOMPLETE"


def test_determine_storage_health_blockers_and_query_errors_is_blocked():
    """A confirmed blocker takes precedence over an unrelated query error."""
    assert determine_storage_health(["PVC blocked"], [], ["events: query failed"]) == "BLOCKED"


def test_determine_storage_health_warnings_and_query_errors_is_warning():
    """A confirmed warning (no blockers) takes precedence over an unrelated query error."""
    assert determine_storage_health([], ["PV orphaned"], ["events: query failed"]) == "WARNING"


# --- Batching/parser tests for the single-Run-Command aks_check_storage (2026-08-31) ---


def _items_json(items: list[dict]) -> str:
    return json.dumps({"items": items})


def _batch_raw_output(sections: dict[str, tuple[int, str]]) -> str:
    """Build the combined multi-section script output aks_check_storage parses in one pass."""
    lines: list[str] = []
    for label, (code, body) in sections.items():
        lines.append(f"===BEGIN:{label}===")
        lines.append(body)
        lines.append(f"===END:{label}:EXIT={code}===")
    return "\n".join(lines)


def _healthy_raw_output() -> str:
    empty_items = _items_json([])
    return _batch_raw_output(
        {
            "pvc": (0, empty_items),
            "pv": (0, empty_items),
            "storageclass": (0, empty_items),
            "pods": (0, ""),
            "events": (0, ""),
        }
    )


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
    """All 5 queries must be issued via exactly one run_kubectl_raw call (one script)."""
    call_count = {"n": 0}

    def fake_raw(subscription_id, resource_group, cluster_name, script):
        call_count["n"] += 1
        for label in ("pvc", "pv", "storageclass", "pods", "events"):
            assert f"===BEGIN:{label}===" in script
        assert "-o jsonpath=" in script  # pods/events must use compact rows, never -o json
        return _healthy_raw_output()

    monkeypatch.setattr(storage, "run_kubectl_raw", fake_raw)

    result = aks_check_storage("sub", "rg", "cluster")

    assert call_count["n"] == 1
    assert result["run_command_invocations"] == 1
    assert result["storage_health"] == "HEALTHY"
    assert result["query_errors"] == []


def test_aks_check_storage_query_failure_is_not_hidden_as_healthy(monkeypatch):
    """A failed PVC query must surface as a query_error, must make storage_health INCOMPLETE
    (not HEALTHY/BLOCKED/WARNING), and must NOT produce a false 'No storage issues detected'
    recommendation."""
    raw = _batch_raw_output(
        {
            "pvc": (1, ""),
            "pv": (0, _items_json([])),
            "storageclass": (0, _items_json([])),
            "pods": (0, ""),
            "events": (0, ""),
        }
    )
    monkeypatch.setattr(storage, "run_kubectl_raw", lambda *a, **k: raw)

    result = aks_check_storage("sub", "rg", "cluster")

    assert result["blockers"] == []
    assert result["warnings"] == []
    assert len(result["query_errors"]) == 1
    assert "pvc" in result["query_errors"][0]
    assert result["storage_health"] == "INCOMPLETE"
    assert not any("No storage issues detected" in r for r in result["recommendations"])
    assert any("could not be executed" in r for r in result["recommendations"])


def test_aks_check_storage_events_failure_stays_best_effort(monkeypatch):
    """Events failing must not crash the tool and must still populate events_available/error,
    while also being visible in the unified query_errors list and forcing INCOMPLETE (not
    HEALTHY)."""
    raw = _batch_raw_output(
        {
            "pvc": (0, _items_json([])),
            "pv": (0, _items_json([])),
            "storageclass": (0, _items_json([])),
            "pods": (0, ""),
            "events": (1, ""),
        }
    )
    monkeypatch.setattr(storage, "run_kubectl_raw", lambda *a, **k: raw)

    result = aks_check_storage("sub", "rg", "cluster")

    assert result["storage_events"]["events_available"] is False
    assert result["storage_events"]["error"] is not None
    assert len(result["query_errors"]) == 1
    assert "events" in result["query_errors"][0]
    assert result["storage_health"] == "INCOMPLETE"


def test_aks_check_storage_missing_pods_and_events_reports_incomplete_not_healthy(monkeypatch):
    """Reproduces the real-cluster bug: pods/events sections entirely absent from the combined
    batch output (as observed when AKS Run Command's own output-size limit truncated a
    cluster-wide response) must yield storage_health == INCOMPLETE, never HEALTHY."""
    raw = _batch_raw_output(
        {
            "pvc": (0, _items_json([])),
            "pv": (0, _items_json([])),
            "storageclass": (0, _items_json([])),
        }
    )  # pods/events sections are entirely missing, exactly as observed on the real cluster
    monkeypatch.setattr(storage, "run_kubectl_raw", lambda *a, **k: raw)

    result = aks_check_storage("sub", "rg", "cluster")

    assert result["storage_health"] == "INCOMPLETE"
    assert any("pods" in err for err in result["query_errors"])
    assert any("events" in err for err in result["query_errors"])
    assert not any("No storage issues detected" in r for r in result["recommendations"])


# --- Compact pod/event jsonpath row parsing (2026-08-31 output-size fix) ---


def test_parse_pod_storage_rows_compact_output_parsed_correctly():
    raw = "payments|db-pod|Pending|FailedMount^Unable to mount volumes for pod~|invoices~\n"

    pods, malformed = _parse_pod_storage_rows(raw)

    assert malformed == 0
    assert len(pods) == 1
    pod = pods[0]
    assert pod["metadata"]["namespace"] == "payments"
    assert pod["metadata"]["name"] == "db-pod"
    assert pod["status"]["phase"] == "Pending"
    assert pod["status"]["containerStatuses"] == [
        {"state": {"waiting": {"reason": "FailedMount", "message": "Unable to mount volumes for pod"}}}
    ]
    assert pod["spec"]["volumes"] == [{"persistentVolumeClaim": {"claimName": "invoices"}}]

    # Round-trip through the real classification functions to prove the reconstructed shape works.
    failures = find_pod_storage_failures(pods)
    assert len(failures) == 1
    assert failures[0]["namespace"] == "payments"


def test_parse_pod_storage_rows_healthy_pod_has_no_containers_or_volumes():
    raw = "payments|api-1|Running||\n"

    pods, malformed = _parse_pod_storage_rows(raw)

    assert malformed == 0
    assert pods[0]["status"]["containerStatuses"] == []
    assert pods[0]["spec"]["volumes"] == []


def test_parse_pod_storage_rows_malformed_row_is_counted_not_silently_dropped():
    raw = "payments|db-pod|Pending\nbillie|ok-pod|Running||\n"  # first row missing fields

    pods, malformed = _parse_pod_storage_rows(raw)

    assert malformed == 1
    assert len(pods) == 1
    assert pods[0]["metadata"]["name"] == "ok-pod"


def test_parse_event_storage_rows_compact_output_parsed_correctly():
    raw = "payments|Pod|db-pod|FailedAttachVolume|AttachVolume.Attach failed for volume pv-1|2026-08-30T00:00:00Z|\n"

    events, malformed = _parse_event_storage_rows(raw)

    assert malformed == 0
    assert len(events) == 1
    event = events[0]
    assert event["reason"] == "FailedAttachVolume"
    assert event["involvedObject"]["kind"] == "Pod"
    assert event["involvedObject"]["name"] == "db-pod"

    # Round-trip through the real classification function.
    storage_events = find_storage_events(events)
    assert len(storage_events) == 1
    assert storage_events[0]["reason"] == "FailedAttachVolume"


def test_parse_event_storage_rows_malformed_row_is_counted_not_silently_dropped():
    raw = "payments|Pod|db-pod|FailedAttachVolume\n"  # missing trailing fields

    events, malformed = _parse_event_storage_rows(raw)

    assert malformed == 1
    assert events == []


def test_extract_pod_storage_rows_missing_label_is_a_query_error():
    errors: list[str] = []

    pods = _extract_pod_storage_rows({}, errors)

    assert pods == []
    assert "pods" in errors[0]
    assert "no result returned" in errors[0]


def test_extract_event_storage_rows_missing_label_is_a_query_error():
    errors: list[str] = []

    events = _extract_event_storage_rows({}, errors)

    assert events == []
    assert "events" in errors[0]
    assert "no result returned" in errors[0]


def test_extract_pod_storage_rows_nonzero_exit_is_a_query_error():
    errors: list[str] = []

    pods = _extract_pod_storage_rows({"pods": (1, "")}, errors)

    assert pods == []
    assert "kubectl exited with code 1" in errors[0]


def test_parse_storage_batch_output_extracts_all_sections():
    raw = _healthy_raw_output()

    batch = _parse_storage_batch_output(raw)

    assert set(batch.keys()) == {"pvc", "pv", "storageclass", "pods", "events"}
    assert batch["pods"] == (0, "")
