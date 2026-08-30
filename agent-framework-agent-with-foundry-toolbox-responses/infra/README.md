# Infrastructure (Phase 3 and 4)

This folder now includes Phase 3 identity and AKS authorization wiring, plus Phase 4 Azure Functions hosting resources.

## What it deploys

- User-assigned managed identity for the AKS MCP service.
- Azure RBAC role assignments on an existing AKS cluster resource.
- Azure Functions hosting resources for the AKS MCP service:
	- Storage account
	- Consumption App Service plan
	- Function App with user-assigned managed identity attached
	- Log Analytics workspace and Application Insights

## Required input

Set the existing AKS resource ID in your azd environment before provisioning.

Example:

```powershell
azd env set EXISTING_AKS_CLUSTER_RESOURCE_ID "/subscriptions/bb0e2c9e-d7fb-45e4-92cf-654f380e6388/resourceGroups/AKS_Upgrade_Agent/providers/Microsoft.ContainerService/managedClusters/AKS_oldapp_cluster"
```

## Optional input

- ASSIGN_AKS_ROLES: defaults to true.
- AKS_ROLE_DEFINITION_GUIDS: defaults to Reader + Azure Kubernetes Service Cluster User Role.
- DEPLOY_AKS_MCP_FUNCTION: defaults to true.
- AKS_MCP_FUNCTION_STORAGE_ACCOUNT_NAME: optional explicit storage account name.
- AKS_MCP_FUNCTION_PLAN_NAME: optional explicit hosting plan name.
- AKS_MCP_FUNCTION_APP_NAME: optional explicit Function App name.
- AKS_MCP_LOG_ANALYTICS_WORKSPACE_NAME: optional explicit Log Analytics workspace name.
- AKS_MCP_APPLICATION_INSIGHTS_NAME: optional explicit Application Insights name.

## Notes

- Reader allows cluster and node pool metadata reads.
- Cluster User Role allows user credential retrieval patterns when needed.
- If you require AKS run-command API permissions, add an additional role definition GUID with that action.

## Kubernetes RBAC bootstrap

After provisioning, map the AKS MCP identity to in-cluster read permissions required by health checks.

PowerShell:

```powershell
./infra/scripts/bootstrap-k8s-rbac.ps1 \
	-SubscriptionId <subId> \
	-AksResourceGroup <aksRg> \
	-AksClusterName <aksName> \
	-SubjectName <identity-principal-object-id>
```

Bash:

```bash
./infra/scripts/bootstrap-k8s-rbac.sh \
	<subId> <aksRg> <aksName> <identity-principal-object-id>
```

Defaults create:

- ClusterRole: `aks-operations-readonly`
- ClusterRoleBinding: `aks-operations-readonly-binding`

Rules include read access for:

- nodes
- pods and pods/status
- namespaces
- poddisruptionbudgets

## Next infrastructure steps (Phase 4)

- Add diagnostics settings export targets, if central monitoring sinks are required.

## Automated MCP deployment with azd

`azure.yaml` includes a `postdeploy` hook that zip-deploys `src/aks-operations-mcp` to the created Function App.

- Windows hook: `scripts/deploy-aks-mcp-function.ps1`
- POSIX hook: `scripts/deploy-aks-mcp-function.sh`

The hook resolves:

- `AKS_MCP_FUNCTION_APP_NAME` from infra outputs
- `AZURE_RESOURCE_GROUP` from azd environment

After `azd up` or `azd deploy`, the MCP endpoint should be:

- `https://<function-app-name>.azurewebsites.net/api/mcp`
