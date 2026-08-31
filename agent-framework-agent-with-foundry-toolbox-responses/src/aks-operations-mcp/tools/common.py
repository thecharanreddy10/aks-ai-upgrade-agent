"""Shared AKS client and command helpers for MCP tools."""

from __future__ import annotations

import json
import os
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.mgmt.containerservice import ContainerServiceClient

try:
    from azure.mgmt.containerservice.models import ManagedClusterRunCommandRequest as RunCommandRequest
except ImportError:
    from azure.mgmt.containerservice.models import RunCommandRequest


def get_container_service_client(subscription_id: str) -> ContainerServiceClient:
    """Create a ContainerServiceClient using managed identity/default credentials.

    If AZURE_CLIENT_ID is set, it pins DefaultAzureCredential to that user-assigned
    identity so resolution is unambiguous if more than one identity is ever attached.
    """
    client_id = os.getenv("AZURE_CLIENT_ID")
    credential = DefaultAzureCredential(managed_identity_client_id=client_id) if client_id else DefaultAzureCredential()
    return ContainerServiceClient(credential=credential, subscription_id=subscription_id)


def run_kubectl_json(
    subscription_id: str,
    resource_group: str,
    cluster_name: str,
    kubectl_arguments: str,
) -> dict[str, Any]:
    """Run a kubectl command through AKS run command and parse JSON output."""
    client = get_container_service_client(subscription_id)

    full_command = f"kubectl {kubectl_arguments} -o json"
    request_obj = RunCommandRequest(command=full_command)
    try:
        poller = client.managed_clusters.begin_run_command(
            resource_group_name=resource_group,
            resource_name=cluster_name,
            request_payload=request_obj,
        )
    except TypeError:
        poller = client.managed_clusters.begin_run_command(
            resource_group_name=resource_group,
            resource_name=cluster_name,
            request=request_obj,
        )
    result = poller.result()

    raw_logs = getattr(result, "logs", None)
    if not raw_logs:
        raise RuntimeError("AKS run command did not return logs output.")

    payload = _extract_json_payload(raw_logs)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        # AKS Run Command has a known output size limit (observed at 524288 bytes); output at or
        # near that size is likely truncated mid-object rather than genuinely malformed JSON.
        truncation_hint = (
            " Output length is at/near AKS Run Command's known output size limit; the result was "
            "likely truncated. Retry with a namespace-scoped query (-n <namespace>) instead of -A."
            if len(raw_logs) >= 524288
            else ""
        )
        raise RuntimeError(
            f"Failed to parse kubectl JSON output for '{full_command}' (raw output length={len(raw_logs)}): {exc}."
            f"{truncation_hint}"
        ) from exc


def _extract_json_payload(output: str) -> str:
    """Extract the first JSON document from mixed command output."""
    first_obj = output.find("{")
    first_arr = output.find("[")

    candidates = [idx for idx in (first_obj, first_arr) if idx != -1]
    if not candidates:
        raise ValueError("No JSON payload found in command output.")

    start = min(candidates)
    end_obj = output.rfind("}")
    end_arr = output.rfind("]")
    end = max(end_obj, end_arr)

    if end < start:
        raise ValueError("Invalid JSON boundaries in command output.")

    return output[start : end + 1]
