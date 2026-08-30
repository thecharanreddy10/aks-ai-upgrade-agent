targetScope = 'resourceGroup'

@description('Name of the existing AKS cluster in this resource group.')
param aksClusterName string

@description('Principal ID of the identity receiving permissions.')
param principalId string

@description('Deterministic seed so redeployments reuse the same custom role definition.')
param roleDefinitionNameSeed string = 'aks-mcp-runcommand'

resource aksCluster 'Microsoft.ContainerService/managedClusters@2024-10-01' existing = {
  name: aksClusterName
}

// AKS Run Command requires exactly these two actions (Microsoft Learn: "Access a private AKS
// cluster using command invoke/Run Command"). No built-in role grants only this pair without also
// granting broader access (e.g. Cluster Admin Role also grants listClusterAdminCredential/action;
// Contributor-tier AKS roles grant full managedClusters/* including delete), so a custom role is used.
resource runCommandRoleDefinition 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(aksCluster.id, roleDefinitionNameSeed)
  properties: {
    roleName: 'AKS MCP Run Command Operator'
    description: 'Minimum permissions to execute AKS Run Command and read its results, scoped to a single cluster.'
    type: 'CustomRole'
    assignableScopes: [
      aksCluster.id
    ]
    permissions: [
      {
        actions: [
          'Microsoft.ContainerService/managedClusters/runcommand/action'
          'Microsoft.ContainerService/managedClusters/commandResults/read'
        ]
        notActions: []
        dataActions: []
        notDataActions: []
      }
    ]
  }
}

resource runCommandRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aksCluster.id, principalId, runCommandRoleDefinition.id)
  scope: aksCluster
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: runCommandRoleDefinition.id
  }
}

output runCommandRoleDefinitionId string = runCommandRoleDefinition.id
