"""Deprecated/removed Kubernetes API detection for AKS upgrade pre-assessment.

Detection approach (see POC-PROGRESS.md for the full rationale):
- `kubectl api-resources` alone only shows what the CURRENT server serves; it cannot tell us
  whether an API still in use will be removed by a future target version. Instead, for each
  entry in KNOWN_API_DEPRECATIONS below we run a targeted `kubectl get <plural>.<version>.<group>`
  query (reusing tools.common.run_kubectl_json / AKS Run Command, same as every other tool in
  this package) to find real objects still using that old apiVersion.
- The target Kubernetes version is compared against each entry's documented deprecated_in/
  removed_in Kubernetes release to classify severity. This mapping cannot be derived from the
  live cluster (the target version doesn't exist yet from the cluster's point of view), so a
  small, explicitly maintained table is required - kept intentionally short (well-known,
  high-impact removals only) rather than an exhaustive compatibility matrix.
- Source: https://kubernetes.io/docs/reference/using-api/deprecation-guide/
"""

from __future__ import annotations

import re
from typing import Any

from tools.common import run_kubectl_json
from tools.discovery import aks_get_cluster_details

# Each entry: the deprecated/removed GroupVersionKind, whether it's namespaced, the Kubernetes
# (major, minor) release it was deprecated in / removed in (None if not applicable), and the
# recommended replacement apiVersion. Deliberately limited to well-known, high-impact API
# removals rather than an exhaustive historical matrix - extend this list as needed.
KNOWN_API_DEPRECATIONS: list[dict[str, Any]] = [
    {"group": "extensions", "version": "v1beta1", "kind": "Ingress", "plural": "ingresses", "namespaced": True,
     "deprecated_in": (1, 14), "removed_in": (1, 22), "replacement": "networking.k8s.io/v1 Ingress"},
    {"group": "networking.k8s.io", "version": "v1beta1", "kind": "Ingress", "plural": "ingresses", "namespaced": True,
     "deprecated_in": (1, 19), "removed_in": (1, 22), "replacement": "networking.k8s.io/v1 Ingress"},
    {"group": "batch", "version": "v1beta1", "kind": "CronJob", "plural": "cronjobs", "namespaced": True,
     "deprecated_in": (1, 21), "removed_in": (1, 25), "replacement": "batch/v1 CronJob"},
    {"group": "policy", "version": "v1beta1", "kind": "PodDisruptionBudget", "plural": "poddisruptionbudgets", "namespaced": True,
     "deprecated_in": (1, 21), "removed_in": (1, 25), "replacement": "policy/v1 PodDisruptionBudget"},
    {"group": "policy", "version": "v1beta1", "kind": "PodSecurityPolicy", "plural": "podsecuritypolicies", "namespaced": False,
     "deprecated_in": (1, 21), "removed_in": (1, 25), "replacement": "Pod Security Admission (PSA) namespace labels"},
    {"group": "scheduling.k8s.io", "version": "v1beta1", "kind": "PriorityClass", "plural": "priorityclasses", "namespaced": False,
     "deprecated_in": (1, 14), "removed_in": (1, 22), "replacement": "scheduling.k8s.io/v1 PriorityClass"},
    {"group": "admissionregistration.k8s.io", "version": "v1beta1", "kind": "MutatingWebhookConfiguration", "plural": "mutatingwebhookconfigurations", "namespaced": False,
     "deprecated_in": (1, 16), "removed_in": (1, 22), "replacement": "admissionregistration.k8s.io/v1 MutatingWebhookConfiguration"},
    {"group": "admissionregistration.k8s.io", "version": "v1beta1", "kind": "ValidatingWebhookConfiguration", "plural": "validatingwebhookconfigurations", "namespaced": False,
     "deprecated_in": (1, 16), "removed_in": (1, 22), "replacement": "admissionregistration.k8s.io/v1 ValidatingWebhookConfiguration"},
    {"group": "apiextensions.k8s.io", "version": "v1beta1", "kind": "CustomResourceDefinition", "plural": "customresourcedefinitions", "namespaced": False,
     "deprecated_in": (1, 16), "removed_in": (1, 22), "replacement": "apiextensions.k8s.io/v1 CustomResourceDefinition"},
    {"group": "apiregistration.k8s.io", "version": "v1beta1", "kind": "APIService", "plural": "apiservices", "namespaced": False,
     "deprecated_in": (1, 16), "removed_in": (1, 22), "replacement": "apiregistration.k8s.io/v1 APIService"},
    {"group": "rbac.authorization.k8s.io", "version": "v1beta1", "kind": "ClusterRole", "plural": "clusterroles", "namespaced": False,
     "deprecated_in": (1, 17), "removed_in": (1, 22), "replacement": "rbac.authorization.k8s.io/v1 ClusterRole"},
    {"group": "rbac.authorization.k8s.io", "version": "v1beta1", "kind": "ClusterRoleBinding", "plural": "clusterrolebindings", "namespaced": False,
     "deprecated_in": (1, 17), "removed_in": (1, 22), "replacement": "rbac.authorization.k8s.io/v1 ClusterRoleBinding"},
    {"group": "certificates.k8s.io", "version": "v1beta1", "kind": "CertificateSigningRequest", "plural": "certificatesigningrequests", "namespaced": False,
     "deprecated_in": (1, 19), "removed_in": (1, 22), "replacement": "certificates.k8s.io/v1 CertificateSigningRequest"},
    {"group": "coordination.k8s.io", "version": "v1beta1", "kind": "Lease", "plural": "leases", "namespaced": True,
     "deprecated_in": (1, 19), "removed_in": (1, 22), "replacement": "coordination.k8s.io/v1 Lease"},
    {"group": "discovery.k8s.io", "version": "v1beta1", "kind": "EndpointSlice", "plural": "endpointslices", "namespaced": True,
     "deprecated_in": (1, 21), "removed_in": (1, 25), "replacement": "discovery.k8s.io/v1 EndpointSlice"},
    {"group": "autoscaling", "version": "v2beta1", "kind": "HorizontalPodAutoscaler", "plural": "horizontalpodautoscalers", "namespaced": True,
     "deprecated_in": (1, 18), "removed_in": (1, 22), "replacement": "autoscaling/v2 HorizontalPodAutoscaler"},
    {"group": "autoscaling", "version": "v2beta2", "kind": "HorizontalPodAutoscaler", "plural": "horizontalpodautoscalers", "namespaced": True,
     "deprecated_in": (1, 23), "removed_in": (1, 26), "replacement": "autoscaling/v2 HorizontalPodAutoscaler"},
]

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)")


def _parse_major_minor(version: str) -> tuple[int, int]:
    """Parse a Kubernetes version string (e.g. '1.27.7' or 'v1.27') into (major, minor)."""
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"Invalid Kubernetes version: {version!r}")
    match = _VERSION_RE.match(version.strip())
    if not match:
        raise ValueError(f"Invalid Kubernetes version format: {version!r}")
    return (int(match.group(1)), int(match.group(2)))


def _format_version(major_minor: tuple[int, int]) -> str:
    return f"{major_minor[0]}.{major_minor[1]}"


def classify_entry(entry: dict[str, Any], target_major_minor: tuple[int, int], target_version_label: str) -> dict[str, Any] | None:
    """Classify a matrix entry against the target version. Returns None if not yet relevant."""
    removed_in = entry.get("removed_in")
    deprecated_in = entry.get("deprecated_in")
    api_version = f"{entry['group']}/{entry['version']}"

    if removed_in is not None and target_major_minor >= removed_in:
        return {
            "severity": "BLOCKER",
            "status": "REMOVED_IN_TARGET",
            "reason": (
                f"{api_version} {entry['kind']} was removed starting in Kubernetes {_format_version(removed_in)}; "
                f"it will not be served by target version {target_version_label}."
            ),
            "recommended_action": f"Migrate to {entry['replacement']} before upgrading to {target_version_label}.",
        }

    if deprecated_in is not None and target_major_minor >= deprecated_in:
        removal_note = f" and is scheduled for removal in {_format_version(removed_in)}" if removed_in else ""
        return {
            "severity": "WARNING",
            "status": "DEPRECATED_STILL_SERVED",
            "reason": (
                f"{api_version} {entry['kind']} has been deprecated since Kubernetes {_format_version(deprecated_in)}"
                f"{removal_note}; still served at target version {target_version_label} but should be migrated."
            ),
            "recommended_action": f"Migrate to {entry['replacement']} ahead of its eventual removal.",
        }

    return None


def determine_deprecated_api_health(blockers: list[str], warnings: list[str]) -> str:
    if blockers:
        return "BLOCKED"
    if warnings:
        return "WARNING"
    return "HEALTHY"


def aks_check_deprecated_apis(
    subscription_id: str,
    resource_group: str,
    cluster_name: str,
    target_version: str | None = None,
    namespace: str | None = None,
) -> dict[str, Any]:
    """Detect Kubernetes API usage that is deprecated or removed relative to a target version.

    If target_version is not supplied, the cluster's own current kubernetes_version is used
    (via aks_get_cluster_details) as the safest non-invented reference point - this reports
    what is already deprecated/removed as of today rather than guessing a future upgrade target.
    """
    if target_version is not None:
        target_major_minor = _parse_major_minor(target_version)
        resolved_target_version = target_version
        target_version_source = "user_provided"
    else:
        cluster = aks_get_cluster_details(subscription_id, resource_group, cluster_name)
        resolved_target_version = cluster["kubernetes_version"]
        target_major_minor = _parse_major_minor(resolved_target_version)
        target_version_source = "cluster_current_version (no target_version provided)"

    ns_flag = f"-n {namespace}" if namespace else "-A"

    findings: list[dict[str, Any]] = []
    query_errors: list[str] = []
    checked_api_versions: list[str] = []

    for entry in KNOWN_API_DEPRECATIONS:
        classification = classify_entry(entry, target_major_minor, resolved_target_version)
        if classification is None:
            continue

        api_version = f"{entry['group']}/{entry['version']}"
        checked_api_versions.append(f"{api_version} {entry['kind']}")
        scope_flag = ns_flag if entry["namespaced"] else ""
        get_command = f"get {entry['plural']}.{entry['version']}.{entry['group']} {scope_flag}".strip()

        try:
            payload = run_kubectl_json(subscription_id, resource_group, cluster_name, get_command)
        except Exception as exc:  # noqa: BLE001 - API/resource absent or unqueryable is informational, not fatal
            query_errors.append(f"{api_version} {entry['kind']}: {exc}")
            continue

        for item in payload.get("items", []):
            metadata = item.get("metadata", {})
            findings.append(
                {
                    "namespace": metadata.get("namespace"),
                    "name": metadata.get("name"),
                    "kind": entry["kind"],
                    "api_version": api_version,
                    "target_kubernetes_version": resolved_target_version,
                    "severity": classification["severity"],
                    "status": classification["status"],
                    "reason": classification["reason"],
                    "recommended_action": classification["recommended_action"],
                }
            )

    blockers: list[str] = []
    warnings: list[str] = []
    for finding in findings:
        identifier = f"{finding['namespace']}/{finding['name']}" if finding["namespace"] else finding["name"]
        message = f"{finding['kind']} {identifier} ({finding['api_version']}): {finding['reason']}"
        (blockers if finding["severity"] == "BLOCKER" else warnings).append(message)

    recommendations: list[str] = []
    if blockers:
        recommendations.append("Migrate resources using removed API versions before proceeding with the upgrade.")
    if warnings:
        recommendations.append("Plan migration for deprecated-but-still-served API versions.")
    if query_errors:
        recommendations.append("Some deprecated-API checks could not be executed; results may be incomplete.")
    if not blockers and not warnings:
        recommendations.append("No deprecated or removed Kubernetes API usage detected for the target version.")

    return {
        "subscription_id": subscription_id,
        "resource_group": resource_group,
        "cluster_name": cluster_name,
        "scope": namespace or "all-namespaces",
        "target_kubernetes_version": resolved_target_version,
        "target_version_source": target_version_source,
        "deprecated_api_health": determine_deprecated_api_health(blockers, warnings),
        "checked_api_versions": checked_api_versions,
        "findings": findings,
        "query_errors": query_errors,
        "blockers": blockers,
        "warnings": warnings,
        "recommendations": recommendations,
    }
