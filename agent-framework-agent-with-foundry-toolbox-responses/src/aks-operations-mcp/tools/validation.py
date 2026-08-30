"""Validation tools for AKS operations."""

from typing import Any

from tools.common import run_kubectl_json


def aks_check_node_health(
    subscription_id: str,
    resource_group: str,
    cluster_name: str,
) -> dict[str, Any]:
    """Check node readiness and pressure conditions."""
    payload = run_kubectl_json(subscription_id, resource_group, cluster_name, "get nodes")
    items = payload.get("items", [])

    unhealthy_nodes = []
    for node in items:
        node_name = node.get("metadata", {}).get("name")
        conditions = node.get("status", {}).get("conditions", [])
        condition_map = {item.get("type"): item.get("status") for item in conditions}

        not_ready = condition_map.get("Ready") != "True"
        pressure_flags = {
            "memory_pressure": condition_map.get("MemoryPressure") == "True",
            "disk_pressure": condition_map.get("DiskPressure") == "True",
            "pid_pressure": condition_map.get("PIDPressure") == "True",
        }

        if not_ready or any(pressure_flags.values()):
            unhealthy_nodes.append(
                {
                    "name": node_name,
                    "ready": not not_ready,
                    **pressure_flags,
                }
            )

    return {
        "cluster_name": cluster_name,
        "total_nodes": len(items),
        "healthy_nodes": len(items) - len(unhealthy_nodes),
        "unhealthy_nodes": unhealthy_nodes,
    }


def aks_check_pod_health(
    subscription_id: str,
    resource_group: str,
    cluster_name: str,
    namespace: str | None = None,
) -> dict[str, Any]:
    """Check pod status, restart counts, and scheduling issues."""
    ns_flag = f"-n {namespace}" if namespace else "-A"
    payload = run_kubectl_json(subscription_id, resource_group, cluster_name, f"get pods {ns_flag}")
    items = payload.get("items", [])

    unhealthy_pods = []
    for pod in items:
        metadata = pod.get("metadata", {})
        status = pod.get("status", {})
        phase = status.get("phase")
        container_statuses = status.get("containerStatuses", [])
        total_restarts = sum(item.get("restartCount", 0) for item in container_statuses)

        waiting_reasons = [
            item.get("state", {}).get("waiting", {}).get("reason")
            for item in container_statuses
            if item.get("state", {}).get("waiting", {}).get("reason")
        ]

        is_unhealthy = phase not in ("Running", "Succeeded") or bool(waiting_reasons)
        if is_unhealthy:
            unhealthy_pods.append(
                {
                    "namespace": metadata.get("namespace"),
                    "name": metadata.get("name"),
                    "phase": phase,
                    "restart_count": total_restarts,
                    "waiting_reasons": waiting_reasons,
                }
            )

    return {
        "cluster_name": cluster_name,
        "scope": namespace or "all-namespaces",
        "total_pods": len(items),
        "healthy_pods": len(items) - len(unhealthy_pods),
        "unhealthy_pods": unhealthy_pods,
    }


def aks_check_pdb(
    subscription_id: str,
    resource_group: str,
    cluster_name: str,
    namespace: str | None = None,
) -> dict[str, Any]:
    """Check Pod Disruption Budget constraints before upgrades."""
    ns_flag = f"-n {namespace}" if namespace else "-A"
    payload = run_kubectl_json(subscription_id, resource_group, cluster_name, f"get pdb {ns_flag}")
    items = payload.get("items", [])

    blocking_pdbs = []
    for pdb in items:
        metadata = pdb.get("metadata", {})
        status = pdb.get("status", {})

        disruptions_allowed = int(status.get("disruptionsAllowed", 0) or 0)
        expected_pods = int(status.get("expectedPods", 0) or 0)
        current_healthy = int(status.get("currentHealthy", 0) or 0)
        desired_healthy = int(status.get("desiredHealthy", 0) or 0)

        if expected_pods > 0 and disruptions_allowed == 0:
            blocking_pdbs.append(
                {
                    "namespace": metadata.get("namespace"),
                    "name": metadata.get("name"),
                    "disruptions_allowed": disruptions_allowed,
                    "current_healthy": current_healthy,
                    "desired_healthy": desired_healthy,
                    "expected_pods": expected_pods,
                }
            )

    return {
        "cluster_name": cluster_name,
        "scope": namespace or "all-namespaces",
        "total_pdbs": len(items),
        "blocking_pdbs": blocking_pdbs,
        "is_upgrade_safe": len(blocking_pdbs) == 0,
    }
