"""AKS operations MCP server entrypoint.

Phase 2 exposes discovery and validation tools via HTTP JSON-RPC.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable

from tools.discovery import (
    aks_get_available_upgrades as get_available_upgrades_impl,
    aks_get_cluster_details as get_cluster_details_impl,
    aks_get_node_pools as get_node_pools_impl,
)
from tools.upgrade import aks_upgrade_node_pool as upgrade_node_pool_impl, aks_validate_upgrade_readiness as validate_upgrade_readiness_impl
from tools.validation import (
    aks_check_node_health as check_node_health_impl,
    aks_check_pdb as check_pdb_impl,
    aks_check_pod_health as check_pod_health_impl,
)

TOOLS = {
    "aks_get_cluster_details": get_cluster_details_impl,
    "aks_get_node_pools": get_node_pools_impl,
    "aks_get_available_upgrades": get_available_upgrades_impl,
    "aks_check_node_health": check_node_health_impl,
    "aks_check_pod_health": check_pod_health_impl,
    "aks_check_pdb": check_pdb_impl,
    "aks_validate_upgrade_readiness": validate_upgrade_readiness_impl,
    "aks_upgrade_node_pool": upgrade_node_pool_impl,
}

TOOL_SCHEMAS = {
    "aks_get_cluster_details": {
        "type": "object",
        "properties": {
            "subscription_id": {"type": "string"},
            "resource_group": {"type": "string"},
            "cluster_name": {"type": "string"},
        },
        "required": ["subscription_id", "resource_group", "cluster_name"],
    },
    "aks_get_node_pools": {
        "type": "object",
        "properties": {
            "subscription_id": {"type": "string"},
            "resource_group": {"type": "string"},
            "cluster_name": {"type": "string"},
        },
        "required": ["subscription_id", "resource_group", "cluster_name"],
    },
    "aks_get_available_upgrades": {
        "type": "object",
        "properties": {
            "subscription_id": {"type": "string"},
            "resource_group": {"type": "string"},
            "cluster_name": {"type": "string"},
        },
        "required": ["subscription_id", "resource_group", "cluster_name"],
    },
    "aks_check_node_health": {
        "type": "object",
        "properties": {
            "subscription_id": {"type": "string"},
            "resource_group": {"type": "string"},
            "cluster_name": {"type": "string"},
        },
        "required": ["subscription_id", "resource_group", "cluster_name"],
    },
    "aks_check_pod_health": {
        "type": "object",
        "properties": {
            "subscription_id": {"type": "string"},
            "resource_group": {"type": "string"},
            "cluster_name": {"type": "string"},
            "namespace": {"type": ["string", "null"]},
        },
        "required": ["subscription_id", "resource_group", "cluster_name"],
    },
    "aks_check_pdb": {
        "type": "object",
        "properties": {
            "subscription_id": {"type": "string"},
            "resource_group": {"type": "string"},
            "cluster_name": {"type": "string"},
            "namespace": {"type": ["string", "null"]},
        },
        "required": ["subscription_id", "resource_group", "cluster_name"],
    },
    "aks_validate_upgrade_readiness": {
        "type": "object",
        "properties": {
            "subscription_id": {"type": "string"},
            "resource_group": {"type": "string"},
            "cluster_name": {"type": "string"},
            "namespace": {"type": ["string", "null"]},
            "maintenance_window_start_utc": {"type": ["string", "null"]},
            "maintenance_window_end_utc": {"type": ["string", "null"]},
            "check_mode": {"type": "string", "enum": ["quick", "full"]},
        },
        "required": ["subscription_id", "resource_group", "cluster_name"],
    },
    "aks_upgrade_node_pool": {
        "type": "object",
        "properties": {
            "subscription_id": {"type": "string"},
            "resource_group": {"type": "string"},
            "cluster_name": {"type": "string"},
            "node_pool_name": {"type": "string"},
            "kubernetes_version": {"type": "string"},
            "namespace": {"type": ["string", "null"]},
            "dry_run": {"type": "boolean"},
            "approval_token": {"type": ["string", "null"]},
            "maintenance_window_start_utc": {"type": ["string", "null"]},
            "maintenance_window_end_utc": {"type": ["string", "null"]},
            "check_mode": {"type": "string", "enum": ["quick", "full"]},
        },
        "required": ["subscription_id", "resource_group", "cluster_name", "node_pool_name", "kubernetes_version"],
    },
}


def _json_response(
    handler: BaseHTTPRequestHandler,
    payload: dict[str, Any],
    status_code: int = 200,
) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _create_handler(tools: dict[str, Callable[..., dict[str, Any]]]) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                _json_response(self, {"status": "ok", "service": "aks-operations-mcp"})
                return
            _json_response(self, {"error": {"code": 404, "message": "Not Found"}}, status_code=404)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/mcp":
                _json_response(self, {"error": {"code": 404, "message": "Not Found"}}, status_code=404)
                return

            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(content_length).decode("utf-8")
                request_payload = json.loads(body)
            except (ValueError, json.JSONDecodeError):
                _json_response(self, {"error": {"code": -32700, "message": "Invalid JSON payload."}}, status_code=400)
                return

            req_id = request_payload.get("id")
            method = request_payload.get("method")
            params = request_payload.get("params", {})

            if method == "tools/list":
                tool_entries = [
                    {
                        "name": name,
                        "description": f"AKS operation: {name}",
                        "inputSchema": TOOL_SCHEMAS[name],
                    }
                    for name in sorted(tools.keys())
                ]
                _json_response(self, {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tool_entries}})
                return

            if method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})

                if tool_name not in tools:
                    _json_response(
                        self,
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                        },
                        status_code=404,
                    )
                    return

                try:
                    result = tools[tool_name](**arguments)
                except TypeError as exc:
                    _json_response(
                        self,
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32602, "message": f"Invalid arguments: {exc}"},
                        },
                        status_code=400,
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    _json_response(
                        self,
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32000, "message": str(exc)},
                        },
                        status_code=500,
                    )
                    return

                _json_response(
                    self,
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(result),
                                }
                            ]
                        },
                    },
                )
                return

            _json_response(
                self,
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Unsupported method: {method}"},
                },
                status_code=400,
            )

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    return _Handler


def list_registered_tools() -> list[str]:
    """Return the list of scaffolded tool names."""
    return sorted(TOOLS.keys())


if __name__ == "__main__":
    host = os.getenv("AKS_MCP_HOST", "0.0.0.0")
    port = int(os.getenv("AKS_MCP_PORT", "8000"))
    print(f"Starting AKS MCP server on {host}:{port}")
    HTTPServer((host, port), _create_handler(TOOLS)).serve_forever()
