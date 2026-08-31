"""Storage health tool for AKS operations.

Read-only diagnostics for PVCs, PVs, StorageClasses, and storage-related pod/event
failures that could block application pods from becoming healthy during an upgrade.

Performance note (2026-08-31): the 5 kubectl queries below (PVCs, PVs, StorageClasses, pods,
events) were originally issued as 5 separate AKS Run Command invocations (measured baseline:
~159s namespace-scoped), each paying AKS Run Command's ~25-35s per-invocation overhead. They are
now batched into a SINGLE Run Command (see tools.common.run_kubectl_batch), following the same
principle used to optimize aks_check_deprecated_apis (17 calls -> 1). Each query's kubectl exit
code is tracked separately from its output, so a genuine query failure is preserved as an explicit
query_errors entry rather than silently treated as an empty/healthy result. All existing
classification functions (classify_pvcs, classify_pvs, find_pod_storage_failures,
find_storage_events, summarize_storage_classes) are unchanged and still operate on full `-o json`
item lists, preserving the existing output contract.
"""

from __future__ import annotations

import json
import re
from typing import Any

from tools.common import run_kubectl_batch

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


_NAMESPACE_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


def _validate_namespace(namespace: str) -> None:
    """Reject anything that isn't a valid Kubernetes namespace name (RFC 1123 label).

    namespace ends up embedded in a shell command run via AKS Run Command, so this also
    prevents shell-metacharacter injection via this parameter.
    """
    if not isinstance(namespace, str) or len(namespace) > 63 or not _NAMESPACE_RE.match(namespace):
        raise ValueError(f"Invalid Kubernetes namespace: {namespace!r}")


def _extract_items(label: str, batch: dict[str, tuple[int, str]], query_errors: list[str]) -> list[dict[str, Any]]:
    """Pull a resource's `items` list out of a batched query result, or record why it's missing.

    A non-zero exit code or unparseable JSON is appended to query_errors and treated as "unknown",
    never as a silent empty/healthy result - the caller must be able to see that this specific
    check could not be confirmed.
    """
    entry = batch.get(label)
    if entry is None:
        query_errors.append(f"{label}: no result returned in the batched output.")
        return []

    exit_code, raw_json = entry
    if exit_code != 0:
        query_errors.append(f"{label}: kubectl exited with code {exit_code}; query could not be executed.")
        return []
    if not raw_json.strip():
        return []

    try:
        return json.loads(raw_json).get("items", [])
    except json.JSONDecodeError as exc:
        truncation_hint = (
            " Output may be truncated near AKS Run Command's output size limit; retry with a "
            "narrower namespace scope."
            if len(raw_json) >= 500_000
            else ""
        )
        query_errors.append(f"{label}: failed to parse kubectl JSON output ({exc}).{truncation_hint}")
        return []


def aks_check_storage(
    subscription_id: str,
    resource_group: str,
    cluster_name: str,
    namespace: str | None = None,
) -> dict[str, Any]:
    """Check PVC/PV/StorageClass/pod/event health for upgrade-blocking storage issues.

    All 5 queries are issued in a single AKS Run Command invocation (see module docstring). If an
    individual query fails, its data is treated as unknown (not empty) and recorded in query_errors;
    events remain best-effort as before (events_available/error), but are also reflected there.
    """
    if namespace is not None:
        _validate_namespace(namespace)

    ns_flag = f"-n {namespace}" if namespace else "-A"

    batch = run_kubectl_batch(
        subscription_id,
        resource_group,
        cluster_name,
        {
            "pvc": f"get pvc {ns_flag}",
            "pv": "get pv",
            "storageclass": "get storageclass",
            "pods": f"get pods {ns_flag}",
            "events": f"get events {ns_flag}",
        },
    )

    query_errors: list[str] = []
    pvc_items = _extract_items("pvc", batch, query_errors)
    pv_items = _extract_items("pv", batch, query_errors)
    sc_items = _extract_items("storageclass", batch, query_errors)
    pod_items = _extract_items("pods", batch, query_errors)

    events_query_errors: list[str] = []
    event_items = _extract_items("events", batch, events_query_errors)
    events_available = not events_query_errors
    events_error = events_query_errors[0] if events_query_errors else None
    query_errors.extend(events_query_errors)

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
    if query_errors:
        recommendations.append("Some storage checks could not be executed; results may be incomplete.")
    if not blockers and not warnings and not query_errors:
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
        "run_command_invocations": 1,
        "query_errors": query_errors,
        "blockers": blockers,
        "warnings": warnings,
        "recommendations": recommendations,
    }

