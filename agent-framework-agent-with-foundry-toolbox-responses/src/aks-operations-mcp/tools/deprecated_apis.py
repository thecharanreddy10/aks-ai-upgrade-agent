"""Deprecated/removed Kubernetes API detection for AKS upgrade pre-assessment.

Detection approach (see POC-PROGRESS.md for the full rationale):
- `kubectl api-resources` alone only shows what the CURRENT server serves; it cannot tell us
  whether an API still in use will be removed by a future target version. Instead, for each
  entry in KNOWN_API_DEPRECATIONS below we check for real objects still using that old apiVersion.
- The target Kubernetes version is compared against each entry's documented deprecated_in/
  removed_in Kubernetes release to classify severity. This mapping cannot be derived from the
  live cluster (the target version doesn't exist yet from the cluster's point of view), so a
  small, explicitly maintained table is required - kept intentionally short (well-known,
  high-impact removals only) rather than an exhaustive compatibility matrix.
- Source: https://kubernetes.io/docs/reference/using-api/deprecation-guide/

Performance note (2026-08-31): AKS Run Command has ~25-35s of per-invocation overhead. The
original implementation issued one Run Command per matrix entry (17 calls, measured at 546s
against the real cluster). This was replaced with a single batched Run Command (see
_build_batch_script) that checks every relevant entry in one invocation, using compact
`-o name` + count output only - never full object JSON - to stay well below AKS Run Command's
output size limit. Each entry's kubectl exit code is captured separately from its output so an
unavailable/removed API (non-zero exit) is never confused with zero matching objects (exit 0,
count 0). This does trade away the previous per-object namespace/name detail (only counts are
now reported per API version) in exchange for the single-invocation design; classification
accuracy (BLOCKER/WARNING) is unchanged.

Correctness note (2026-08-31): real-cluster validation surfaced a bug where entries with
unavailable/unqueryable APIs (query_errors) could coexist with a "HEALTHY" / "no usage detected"
result, incorrectly implying usage had been confirmed absent. Fixed: determine_deprecated_api_health
now returns "INCOMPLETE" (not "HEALTHY") when there are no confirmed findings but some entries
could not be checked, and the "no usage detected" recommendation is never emitted when
query_errors is non-empty. A confirmed BLOCKER/WARNING finding still takes precedence over
INCOMPLETE, since it is more actionable - query_errors remain visible in the result either way.
"""

from __future__ import annotations

import re
from typing import Any

from tools.common import run_kubectl_raw
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


def determine_deprecated_api_health(
    blockers: list[str],
    warnings: list[str],
    query_errors: list[str] | None = None,
) -> str:
    """Classify overall deprecated-API health.

    Precedence: a confirmed BLOCKER/WARNING finding is always reported as such, even if some
    other entries were unqueryable (those are still preserved in query_errors for transparency).
    Only when there are NO confirmed findings AND some entries could not be checked does this
    return INCOMPLETE - an unavailable/unqueryable API must never be read as "HEALTHY", since
    that would incorrectly imply usage was confirmed absent when it simply could not be checked.
    """
    if blockers:
        return "BLOCKED"
    if warnings:
        return "WARNING"
    if query_errors:
        return "INCOMPLETE"
    return "HEALTHY"


_NAMESPACE_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


def _validate_namespace(namespace: str) -> None:
    """Reject anything that isn't a valid Kubernetes namespace name (RFC 1123 label).

    namespace ends up embedded in a shell command run via AKS Run Command, so this also
    prevents shell-metacharacter injection via this parameter.
    """
    if not isinstance(namespace, str) or len(namespace) > 63 or not _NAMESPACE_RE.match(namespace):
        raise ValueError(f"Invalid Kubernetes namespace: {namespace!r}")


_ENTRY_BLOCK_RE = re.compile(r"===ENTRY:(\d+)===\s*EXIT=(-?\d+)\s+COUNT=(\d+)")


def _build_batch_script(entries: list[dict[str, Any]], ns_flag: str) -> str:
    """Build one shell script that checks every given matrix entry in a single Run Command.

    For each entry: captures kubectl's exit code separately from its output (so a removed/
    unavailable API - non-zero exit - is never confused with zero matching objects), and emits
    only a compact object count via `-o name` - never full object JSON.
    """
    lines: list[str] = []
    for index, entry in enumerate(entries):
        scope_flag = ns_flag if entry["namespaced"] else ""
        gvk = f"{entry['plural']}.{entry['version']}.{entry['group']}"
        get_args = re.sub(r"\s+", " ", f"get '{gvk}' {scope_flag} --ignore-not-found -o name".strip())
        lines.append(f"echo '===ENTRY:{index}==='")
        lines.append(f"RAW=$(kubectl {get_args} 2>/dev/null)")
        lines.append("CODE=$?")
        lines.append("COUNT=$(printf '%s\\n' \"$RAW\" | grep -c '.')")
        lines.append('echo "EXIT=$CODE COUNT=$COUNT"')
    return "\n".join(lines)


def _parse_batch_output(raw_output: str) -> dict[int, tuple[int, int]]:
    """Parse the compact batched script output into {entry_index: (exit_code, count)}."""
    return {
        int(match.group(1)): (int(match.group(2)), int(match.group(3)))
        for match in _ENTRY_BLOCK_RE.finditer(raw_output)
    }


def aks_check_deprecated_apis(
    subscription_id: str,
    resource_group: str,
    cluster_name: str,
    target_version: str | None = None,
    namespace: str | None = None,
) -> dict[str, Any]:
    """Detect Kubernetes API usage that is deprecated or removed relative to a target version.

    All relevant matrix entries are checked in a SINGLE AKS Run Command invocation (see
    _build_batch_script) rather than one invocation per entry - see the module docstring for why.
    Findings report a count per API version rather than individual namespace/name detail, since
    the batched query only requests compact counts (never full object JSON).

    If target_version is not supplied, the cluster's own current kubernetes_version is used
    (via aks_get_cluster_details) as the safest non-invented reference point - this reports
    what is already deprecated/removed as of today rather than guessing a future upgrade target.

    deprecated_api_health is one of "BLOCKED" / "WARNING" / "INCOMPLETE" / "HEALTHY". "INCOMPLETE"
    means no BLOCKER/WARNING findings were confirmed, but some API queries could not be executed
    (see query_errors) - this must never be conflated with "HEALTHY", since an unavailable API is
    not evidence that no deprecated resources exist.
    """
    if namespace is not None:
        _validate_namespace(namespace)

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

    relevant: list[tuple[dict[str, Any], dict[str, Any]]] = []
    checked_api_versions: list[str] = []
    for entry in KNOWN_API_DEPRECATIONS:
        classification = classify_entry(entry, target_major_minor, resolved_target_version)
        if classification is None:
            continue
        relevant.append((entry, classification))
        checked_api_versions.append(f"{entry['group']}/{entry['version']} {entry['kind']}")

    findings: list[dict[str, Any]] = []
    query_errors: list[str] = []
    run_command_invocations = 0

    if relevant:
        script = _build_batch_script([entry for entry, _classification in relevant], ns_flag)
        try:
            raw_output = run_kubectl_raw(subscription_id, resource_group, cluster_name, script)
            run_command_invocations = 1
        except Exception as exc:  # noqa: BLE001 - batch call failure is informational, not fatal
            query_errors.append(f"batched deprecated-API check failed: {exc}")
            raw_output = ""

        parsed = _parse_batch_output(raw_output) if raw_output else {}

        for index, (entry, classification) in enumerate(relevant):
            api_version = f"{entry['group']}/{entry['version']}"

            if index not in parsed:
                query_errors.append(
                    f"{api_version} {entry['kind']}: no result returned for this entry in the batched output."
                )
                continue

            exit_code, count = parsed[index]
            if exit_code != 0:
                # Non-zero exit means the API/resource type is unavailable on this cluster (e.g.
                # already removed) - this must NOT be read as evidence that no such resources exist.
                query_errors.append(
                    f"{api_version} {entry['kind']}: API not available/servable on this cluster "
                    f"(kubectl exit code {exit_code}); usage could not be confirmed."
                )
                continue

            if count <= 0:
                continue

            findings.append(
                {
                    "kind": entry["kind"],
                    "api_version": api_version,
                    "count": count,
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
        message = (
            f"{finding['kind']} ({finding['api_version']}): {finding['count']} object(s) found. {finding['reason']}"
        )
        (blockers if finding["severity"] == "BLOCKER" else warnings).append(message)

    recommendations: list[str] = []
    if blockers:
        recommendations.append("Migrate resources using removed API versions before proceeding with the upgrade.")
    if warnings:
        recommendations.append("Plan migration for deprecated-but-still-served API versions.")
    if query_errors:
        recommendations.append("Deprecated API assessment is incomplete because some API queries could not be executed.")
    if not blockers and not warnings and not query_errors:
        recommendations.append("No deprecated or removed Kubernetes API usage detected for the target version.")

    return {
        "subscription_id": subscription_id,
        "resource_group": resource_group,
        "cluster_name": cluster_name,
        "scope": namespace or "all-namespaces",
        "target_kubernetes_version": resolved_target_version,
        "target_version_source": target_version_source,
        "deprecated_api_health": determine_deprecated_api_health(blockers, warnings, query_errors),
        "checked_api_versions": checked_api_versions,
        "run_command_invocations": run_command_invocations,
        "findings": findings,
        "query_errors": query_errors,
        "blockers": blockers,
        "warnings": warnings,
        "recommendations": recommendations,
    }

