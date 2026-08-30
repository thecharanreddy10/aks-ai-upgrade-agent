# Copyright (c) Microsoft. All rights reserved.

import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import FoundryToolbox, ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def _run_fallback_http_server() -> None:
    """Run a tiny HTTP endpoint so the container stays healthy when not fully configured."""

    host = os.getenv("AGENT_HOST", "0.0.0.0")
    port = int(os.getenv("AGENT_PORT", "8088"))

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            payload = {
                "status": "degraded",
                "service": "af-foundry-agent",
                "message": "Missing Foundry configuration. Set FOUNDRY_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME.",
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    print(f"Starting fallback HTTP server on {host}:{port}")
    HTTPServer((host, port), _Handler).serve_forever()


async def main():
    project_endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
    model_name = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")

    if not project_endpoint or not model_name:
        print("Foundry model configuration is missing; starting fallback server.")
        _run_fallback_http_server()
        return

    credential = DefaultAzureCredential()

    # Prefer TOOLBOX_ENDPOINT. If absent, allow AKS_MCP_ENDPOINT to point directly
    # to the deployed MCP endpoint for non-Foundry toolbox deployments.
    toolbox_endpoint = os.getenv("TOOLBOX_ENDPOINT") or os.getenv("AKS_MCP_ENDPOINT")
    toolbox = FoundryToolbox(credential, url=toolbox_endpoint) if toolbox_endpoint else None

    # Create the chat client
    client = FoundryChatClient(
        project_endpoint=project_endpoint,
        model=model_name,
        credential=credential,
    )

    agent = Agent(
        client=client,
        instructions=(
            "You are the AKS Upgrade Agent. You assess whether an existing Azure Kubernetes "
            "Service (AKS) cluster is ready for a Kubernetes version upgrade.\n\n"
            "Always use the available MCP tools to gather real cluster data before answering; "
            "never guess or assume cluster state when a tool can provide it. Use the tools to:\n"
            "- Get cluster details and the current Kubernetes version.\n"
            "- Get node pool details.\n"
            "- Check available Kubernetes upgrades.\n"
            "- Check node health.\n"
            "- Check pod health.\n"
            "- Check PodDisruptionBudget (PDB) conditions that could block node draining.\n\n"
            "When asked about upgrade readiness, run the relevant checks first, then clearly "
            "explain any blockers, any warnings, and what still needs to be verified. Never "
            "claim an upgrade is safe without having performed the appropriate checks. Never "
            "execute an AKS upgrade yourself unless the user has explicitly authorized it and "
            "the tool's own safety gates (dry-run, health checks, approval token) allow it to "
            "proceed. Keep answers concise and grounded in tool output."
        ),
        tools=toolbox or [],
        # History will be managed by the hosting infrastructure, thus there
        # is no need to store history by the service. Learn more at:
        # https://developers.openai.com/api/reference/resources/responses/methods/create
        default_options={"store": False},
    )

    server = ResponsesHostServer(agent)
    await server.run_async()


if __name__ == "__main__":
    asyncio.run(main())
