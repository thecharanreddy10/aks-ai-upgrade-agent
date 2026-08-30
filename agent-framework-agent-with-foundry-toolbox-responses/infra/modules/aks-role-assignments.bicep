targetScope = 'resourceGroup'

@description('Name of the existing AKS cluster in this resource group.')
param aksClusterName string

@description('Principal ID of the identity receiving permissions.')
param principalId string

@description('Role definition GUIDs to assign at AKS scope.')
param roleDefinitionGuids array

resource aksCluster 'Microsoft.ContainerService/managedClusters@2024-10-01' existing = {
  name: aksClusterName
}

resource aksRoleAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for roleGuid in roleDefinitionGuids: {
    name: guid(aksCluster.id, principalId, roleGuid)
    scope: aksCluster
    properties: {
      principalId: principalId
      principalType: 'ServicePrincipal'
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleGuid)
    }
  }
]

output assignedRoleDefinitionGuids array = roleDefinitionGuids
