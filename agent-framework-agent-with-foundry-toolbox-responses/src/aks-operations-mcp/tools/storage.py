"""Storage health tool for AKS operations.

Read-only diagnostics for PVCs, PVs, StorageClasses, and storage-related pod/event
failures that could block application pods from becoming healthy during an upgrade.
"""

from __future__ import annotations

from typing import Any

from tools.common import run_kubectl_json

# Event/waiting reasons that are unambiguously storage-related on their own.
_STRONG_STORAGE_REASONS = {
    "FailedMount",
    "FailedAttachVolume",
    "FailedMountVolume",
    "FailedMapVolume",
    "FailedBinding",
    "ProvisioningFailed",
    "VolumeFailedDelete",
}

# Generic keywords that only count as storage-related when paired with a reason/message,
# e.g. so a plain "FailedScheduling" (which can be CPU/memory) isn't misclassified.
_STORAGE_KEYWORDS = ("volume", "mount", "attach", "persistentvolumeclaim", "pvc", "storage", "provision")


def _text_indicates_storage_issue(reason: str | None, message: str | None) -> bool:
    """Return whether a reason/message pair describes a storage-related failure."""
    if reason and reason in _STRONG_STORAGE_REASONS:
        return True
    combined = f"{reason or ''} {message or ''}".lower()
    return any(keyword in combined for keyword in _STORAGE_KEYWORDS)


def find_pod_storage_failures(pod_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find non-running pods whose container state indicates a storage-related failure."""
    failures = []
    for pod in pod_items:
        metadata = pod.get("metadata", {})
        status = pod.get("status", {})
        if status.get("phase") in ("Running", "Succeeded"):
            continue

        for container_status in status.get("containerStatuses", []) or []:
            waiting = container_status.get("state", {}).get("waiting", {}) or {}
            reason = waiting.get("reason")
            message = waiting.get("message")
            if _text_indicates_storage_issue(reason, message):
                failures.append(
                    {
                        "namespace": metadata.get("namespace"),
                        "name": metadata.get("name"),
                        "reason": reason,
                        "message": message,
                    }
                )
                break

    return failures


def find_storage_events(event_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find Kubernetes events that indicate a storage-related warning/error."""
    events = []
    for event in event_items:
        reason = event.get("reason")
        message = event.get("message")
        if not _text_indicates_storage_issue(reason, message):
            continue

        involved = event.get("involvedObject", {}) or {}
        events.append(
            {
                "namespace": event.get("metadata", {}).get("namespace") or involved.get("namespace"),
                "involved_kind": involved.get("kind"),
                "involved_object": involved.get("name"),
                "reason": reason,
                "message": message,
                "last_timestamp": event.get("lastTimestamp") or event.get("eventTime"),
            }
        )

    return events


def summarize_storage_classes(sc_items: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Summarize StorageClasses; not a source of blockers/warnings on its own."""
    items = []
    by_name: dict[str, dict[str, Any]] = {}
    for storage_class in sc_items:
        name = storage_class.get("metadata", {}).get("name")
        info = {
            "name": name,
            "provisioner": storage_class.get("provisioner"),
            "reclaim_policy": storage_class.get("reclaimPolicy"),
            "volume_binding_mode": storage_class.get("volumeBindingMode"),
            "allow_volume_expansion": storage_class.get("allowVolumeExpansion"),
        }
        items.append(info)
        by_name[name] = info

    return {"total": len(items), "items": items}, by_name


def _pvc_names_referenced_by_pods(pod_items: list[dict[str, Any]]) -> set[tuple[str | None, str]]:
    referenced = set()
    for pod in pod_items:
        namespace = pod.get("metadata", {}).get("namespace")
        for volume in pod.get("spec", {}).get("volumes", []) or []:
            claim_name = (volume.get("persistentVolumeClaim") or {}).get("claimName")
            if claim_name:
                referenced.add((namespace, claim_name))

    return referenced


def _pvc_claims_of_failing_pods(
    pod_items: list[dict[str, Any]],
    pod_storage_failures: list[dict[str, Any]],
) -> set[tuple[str | None, str]]:
    failing_pod_keys = {(failure["namespace"], failure["name"]) for failure in pod_storage_failures}
    claims = set()
    for pod in pod_items:
        metadata = pod.get("metadata", {})
        key = (metadata.get("namespace"), metadata.get("name"))
        if key not in failing_pod_keys:
            continue
        for volume in pod.get("spec", {}).get("volumes", []) or []:
            claim_name = (volume.get("persistentVolumeClaim") or {}).get("claimName")
            if claim_name:
                claims.add((metadata.get("namespace"), claim_name))

    return claims


def classify_pvcs(
    pvc_items: list[dict[str, Any]],
    pod_items: list[dict[str, Any]],
    storage_classes_by_name: dict[str, dict[str, Any]] | None = None,
    pod_storage_failures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify PVCs, using pod references/failures for context on Pending claims."""
    storage_classes_by_name = storage_classes_by_name or {}
    referenced = _pvc_names_referenced_by_pods(pod_items)
    actively_failing_claims = _pvc_claims_of_failing_pods(pod_items, pod_storage_failures or [])

    total = 0
    bound = 0
    pending = 0
    problematic: list[dict[str, Any]] = []

    for pvc in pvc_items:
        total += 1
        metadata = pvc.get("metadata", {})
        namespace = metadata.get("namespace")
        name = metadata.get("name")
        spec = pvc.get("spec", {})
        phase = pvc.get("status", {}).get("phase")
        key = (namespace, name)

        if phase == "Bound":
            bound += 1
            if not spec.get("volumeName"):
                problematic.append(
                    {
                        "namespace": namespace,
                        "name": name,
                        "phase": phase,
                        "severity": "BLOCKER",
                        "reason": "PVC reports Bound phase but has no bound volumeName (inconsistent state).",
                    }
                )
            continue

        if phase == "Pending":
            pending += 1
            binding_mode = (storage_classes_by_name.get(spec.get("storageClassName")) or {}).get("volume_binding_mode")
            is_referenced = key in referenced
            is_actively_failing = key in actively_failing_claims

            if is_actively_failing:
                severity = "BLOCKER"
                reason = "PVC is Pending and the pod requiring it is actively failing to mount/attach storage."
            elif is_referenced and binding_mode == "WaitForFirstConsumer":
                severity = "WARNING"
                reason = (
                    "PVC is Pending with WaitForFirstConsumer binding; this is expected until its "
                    "pod is scheduled. Monitor for mount failures."
                )
            elif is_referenced:
                severity = "BLOCKER"
                reason = "PVC is Pending and is required by an existing pod."
            else:
                severity = "WARNING"
                reason = "PVC is Pending but is not currently referenced by any pod."

            problematic.append(
                {"namespace": namespace, "name": name, "phase": phase, "severity": severity, "reason": reason}
            )
            continue

        severity = "BLOCKER" if phase == "Lost" else "WARNING"
        problematic.append(
            {
                "namespace": namespace,
                "name": name,
                "phase": phase,
                "severity": severity,
                "reason": f"PVC is in unexpected phase '{phase}'.",
            }
        )

    return {"total": total, "bound": bound, "pending": pending, "problematic": problematic}


def classify_pvs(pv_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify PVs. An orphaned/unclaimed PV is a warning, never an automatic blocker."""
    total = 0
    bound = 0
    problematic: list[dict[str, Any]] = []

    for pv in pv_items:
        total += 1
        name = pv.get("metadata", {}).get("name")
        phase = pv.get("status", {}).get("phase")
        claim_ref = pv.get("spec", {}).get("claimRef")

        if phase == "Bound":
            bound += 1
            if not claim_ref:
                problematic.append(
                    {
                        "name": name,
                        "phase": phase,
                        "severity": "WARNING",
                        "reason": "PV reports Bound phase but has no claimRef (suspicious state).",
                    }
                )
            continue

        if phase == "Available":
            problematic.append(
                {
                    "name": name,
                    "phase": phase,
                    "severity": "WARNING",
                    "reason": "PV is unclaimed/orphaned (Available). Not currently blocking any workload.",
                }
            )
            continue

        if phase == "Released":
            problematic.append(
                {
                    "name": name,
                    "phase": phase,
                    "severity": "WARNING",
                    "reason": "PV is Released; depending on reclaim policy it may need manual cleanup.",
                }
            )
            continue

        if phase == "Failed":
            problematic.append(
                {"name": name, "phase": phase, "severity": "BLOCKER", "reason": "PV is in Failed state."}
            )
            continue

        problematic.append(
            {"name": name, "phase": phase, "severity": "WARNING", "reason": f"PV is in unexpected phase '{phase}'."}
        )

    return {"total": total, "bound": bound, "unbound_or_problematic": problematic}


def determine_storage_health(blockers: list[str], warnings: list[str]) -> str:
    if blockers:
        return "BLOCKED"
    if warnings:
        return "WARNING"
    return "HEALTHY"


def aks_check_storage(
    subscription_id: str,
    resource_group: str,
    cluster_name: str,
    namespace: str | None = None,
) -> dict[str, Any]:
    """Check PVC/PV/StorageClass/pod/event health for upgrade-blocking storage issues."""
    ns_flag = f"-n {namespace}" if namespace else "-A"

    pvc_items = run_kubectl_json(subscription_id, resource_group, cluster_name, f"get pvc {ns_flag}").get("items", [])
    pv_items = run_kubectl_json(subscription_id, resource_group, cluster_name, "get pv").get("items", [])
    sc_items = run_kubectl_json(subscription_id, resource_group, cluster_name, "get storageclass").get("items", [])
    pod_items = run_kubectl_json(subscription_id, resource_group, cluster_name, f"get pods {ns_flag}").get("items", [])

    events_available = True
    events_error: str | None = None
    event_items: list[dict[str, Any]] = []
    try:
        event_items = run_kubectl_json(subscription_id, resource_group, cluster_name, f"get events {ns_flag}").get(
            "items", []
        )
    except Exception as exc:  # noqa: BLE001 - events are best-effort, never a hard dependency
        events_available = False
        events_error = str(exc)

    storage_classes, storage_classes_by_name = summarize_storage_classes(sc_items)
    pod_storage_failures = find_pod_storage_failures(pod_items)
    storage_events = find_storage_events(event_items)
    pvcs = classify_pvcs(pvc_items, pod_items, storage_classes_by_name, pod_storage_failures)
    pvs = classify_pvs(pv_items)

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

    for event in storage_events:
        warnings.append(
            f"Event for {event.get('involved_kind')}/{event.get('involved_object')} "
            f"in {event.get('namespace') or 'cluster-scope'}: {event.get('reason')} - {event.get('message')}"
        )

    recommendations: list[str] = []
    if blockers:
        recommendations.append("Investigate blocking PVC/PV/pod storage issues before proceeding with an upgrade.")
    if not events_available:
        recommendations.append("Kubernetes events could not be retrieved; storage diagnostics may be incomplete.")
    if not blockers and not warnings:
        recommendations.append("No storage issues detected.")

    return {
        "subscription_id": subscription_id,
        "resource_group": resource_group,
        "cluster_name": cluster_name,
        "scope": namespace or "all-namespaces",
        "storage_health": determine_storage_health(blockers, warnings),
        "pvcs": pvcs,
        "pvs": pvs,
        "storage_classes": storage_classes,
        "pod_storage_failures": {"count": len(pod_storage_failures), "pods": pod_storage_failures},
        "storage_events": {
            "count": len(storage_events),
            "events_available": events_available,
            "events": storage_events,
            "error": events_error,
        },
        "blockers": blockers,
        "warnings": warnings,
        "recommendations": recommendations,
    }
