targetScope = 'resourceGroup'

@description('Azure region for resources that support regional deployment.')
param location string = resourceGroup().location

@description('Prefix used for generated names in later phases.')
param namePrefix string = 'aks-upgrade-agent'

@description('Resource ID of the existing AKS cluster to operate against.')
param existingAksClusterResourceId string = ''

@description('User-assigned managed identity name for AKS MCP service.')
param aksMcpIdentityName string = '${namePrefix}-mcp-mi'

@description('Assign Azure RBAC roles on the target AKS resource when true.')
param assignAksRoles bool = true

@description('Role definition GUIDs to assign at AKS scope. Defaults: Reader, Azure Kubernetes Service Cluster User Role.')
param aksRoleDefinitionGuids array = [
	'acdd72a7-3385-48ef-bd42-f606fba81ae7'
	'4abbcc35-e782-43d8-92c5-2d3f1bd2253f'
]

@description('Deploy Azure Functions hosting resources for the AKS MCP service.')
param deployAksMcpFunction bool = true

@description('Optional explicit storage account name for the AKS MCP Function App.')
param aksMcpFunctionStorageAccountName string = ''

@description('Optional explicit App Service plan name for the AKS MCP Function App.')
param aksMcpFunctionPlanName string = ''

@description('Optional explicit Function App name for the AKS MCP service.')
param aksMcpFunctionAppName string = ''

@description('Optional explicit Log Analytics workspace name used by the AKS MCP service monitoring stack.')
param aksMcpLogAnalyticsWorkspaceName string = ''

@description('Optional explicit Application Insights name for the AKS MCP service monitoring stack.')
param aksMcpApplicationInsightsName string = ''

var aksIdParts = split(existingAksClusterResourceId, '/')
var aksSubscriptionId = !empty(existingAksClusterResourceId) ? aksIdParts[2] : ''
var aksResourceGroup = !empty(existingAksClusterResourceId) ? aksIdParts[4] : ''
var aksClusterName = !empty(existingAksClusterResourceId) ? aksIdParts[8] : ''

var functionStorageAccountName = !empty(aksMcpFunctionStorageAccountName)
	? toLower(aksMcpFunctionStorageAccountName)
	: take('aksmcp${uniqueString(subscription().id, resourceGroup().id, namePrefix)}', 24)
var functionPlanName = !empty(aksMcpFunctionPlanName)
	? aksMcpFunctionPlanName
	: take('${namePrefix}-mcp-fn-plan-${uniqueString(resourceGroup().id)}', 40)
var functionAppName = !empty(aksMcpFunctionAppName)
	? aksMcpFunctionAppName
	: take('${namePrefix}-mcp-fn-${uniqueString(resourceGroup().id)}', 60)
var logAnalyticsWorkspaceName = !empty(aksMcpLogAnalyticsWorkspaceName)
	? aksMcpLogAnalyticsWorkspaceName
	: take('${namePrefix}-mcp-law-${uniqueString(resourceGroup().id)}', 63)
var applicationInsightsName = !empty(aksMcpApplicationInsightsName)
	? aksMcpApplicationInsightsName
	: take('${namePrefix}-mcp-ai-${uniqueString(resourceGroup().id)}', 260)

resource aksMcpIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
	name: aksMcpIdentityName
	location: location
}

resource aksMcpFunctionStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = if (deployAksMcpFunction) {
	name: functionStorageAccountName
	location: location
	sku: {
		name: 'Standard_LRS'
	}
	kind: 'StorageV2'
	properties: {
		allowBlobPublicAccess: false
		minimumTlsVersion: 'TLS1_2'
		supportsHttpsTrafficOnly: true
	}
}

resource aksMcpLogAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = if (deployAksMcpFunction) {
	name: logAnalyticsWorkspaceName
	location: location
	properties: {
		sku: {
			name: 'PerGB2018'
		}
		retentionInDays: 30
	}
}

resource aksMcpAppInsights 'Microsoft.Insights/components@2020-02-02' = if (deployAksMcpFunction) {
	name: applicationInsightsName
	location: location
	kind: 'web'
	properties: {
		Application_Type: 'web'
		WorkspaceResourceId: aksMcpLogAnalytics.id
	}
}

resource aksMcpFunctionPlan 'Microsoft.Web/serverfarms@2023-12-01' = if (deployAksMcpFunction) {
	name: functionPlanName
	location: location
	kind: 'linux'
	sku: {
		name: 'Y1'
		tier: 'Dynamic'
	}
	properties: {
		reserved: true
	}
}

resource aksMcpFunctionApp 'Microsoft.Web/sites@2023-12-01' = if (deployAksMcpFunction) {
	name: functionAppName
	location: location
	kind: 'functionapp,linux'
	identity: {
		type: 'UserAssigned'
		userAssignedIdentities: {
			'${aksMcpIdentity.id}': {}
		}
	}
	properties: {
		serverFarmId: aksMcpFunctionPlan.id
		httpsOnly: true
		siteConfig: {
			linuxFxVersion: 'Python|3.11'
			appSettings: [
				{
					name: 'FUNCTIONS_EXTENSION_VERSION'
					value: '~4'
				}
				{
					name: 'FUNCTIONS_WORKER_RUNTIME'
					value: 'python'
				}
				{
					name: 'AzureWebJobsStorage'
					value: 'DefaultEndpointsProtocol=https;AccountName=${aksMcpFunctionStorage!.name};AccountKey=${aksMcpFunctionStorage!.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
				}
				{
					name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
					value: aksMcpAppInsights!.properties.ConnectionString
				}
				{
					name: 'APPINSIGHTS_INSTRUMENTATIONKEY'
					value: aksMcpAppInsights!.properties.InstrumentationKey
				}
				{
					name: 'WEBSITE_RUN_FROM_PACKAGE'
					value: '1'
				}
				{
					name: 'AKS_MCP_HOST'
					value: '0.0.0.0'
				}
				{
					name: 'AKS_MCP_PORT'
					value: '8000'
				}
			]
		}
	}
}

module aksRoleAssignmentModule './modules/aks-role-assignments.bicep' = if (!empty(existingAksClusterResourceId) && assignAksRoles) {
	name: 'aks-role-assignment-${uniqueString(existingAksClusterResourceId, aksMcpIdentity.id)}'
	scope: resourceGroup(aksSubscriptionId, aksResourceGroup)
	params: {
		aksClusterName: aksClusterName
		principalId: aksMcpIdentity.properties.principalId
		roleDefinitionGuids: aksRoleDefinitionGuids
	}
}

output deploymentLocation string = location
output deploymentNamePrefix string = namePrefix
output targetAksClusterResourceId string = existingAksClusterResourceId
output aksMcpIdentityNameOut string = aksMcpIdentity.name
output aksMcpIdentityPrincipalId string = aksMcpIdentity.properties.principalId
output aksMcpIdentityClientId string = aksMcpIdentity.properties.clientId
output assignedAksRoleDefinitionGuids array = assignAksRoles ? aksRoleDefinitionGuids : []
output aksMcpFunctionAppNameOut string = deployAksMcpFunction ? aksMcpFunctionApp.name : ''
output aksMcpFunctionAppHostName string = deployAksMcpFunction ? aksMcpFunctionApp!.properties.defaultHostName : ''
output aksMcpFunctionAppEndpoint string = deployAksMcpFunction ? 'https://${aksMcpFunctionApp!.properties.defaultHostName}' : ''
output AKS_MCP_FUNCTION_APP_NAME string = deployAksMcpFunction ? aksMcpFunctionApp.name : ''
output AKS_MCP_FUNCTION_APP_ENDPOINT string = deployAksMcpFunction ? 'https://${aksMcpFunctionApp!.properties.defaultHostName}/api/mcp' : ''
output AKS_MCP_IDENTITY_PRINCIPAL_ID string = aksMcpIdentity.properties.principalId
