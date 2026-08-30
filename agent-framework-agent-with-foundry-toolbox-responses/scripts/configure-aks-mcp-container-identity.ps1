param(
    [string]$ContainerAppName = "",
    [string]$ContainerAppResourceGroup = "",
    [string]$IdentityResourceId = "",
    [string]$AksSubscriptionId = "",
    [string]$AksResourceGroup = "",
    [string]$AksClusterName = ""
)

$ErrorActionPreference = 'Stop'

function Get-AzdValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Key,
        [string]$Fallback = ""
    )

    try {
        $value = azd env get-value $Key 2>$null
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value.Trim('"')
        }
    }
    catch {
    }

    return $Fallback
}

if ([string]::IsNullOrWhiteSpace($IdentityResourceId)) {
    $IdentityResourceId = Get-AzdValue -Key "aksMcpContainerAppIdentityResourceId"
}
if ([string]::IsNullOrWhiteSpace($ContainerAppName)) {
    $ContainerAppName = Get-AzdValue -Key "AKS_MCP_CONTAINER_APP_NAME" -Fallback "aks-mcp"
}
if ([string]::IsNullOrWhiteSpace($ContainerAppResourceGroup)) {
    $ContainerAppResourceGroup = Get-AzdValue -Key "AZURE_RESOURCE_GROUP"
}

if ([string]::IsNullOrWhiteSpace($IdentityResourceId) -or [string]::IsNullOrWhiteSpace($ContainerAppName) -or [string]::IsNullOrWhiteSpace($ContainerAppResourceGroup)) {
    throw "Unable to resolve IdentityResourceId, ContainerAppName, or ContainerAppResourceGroup. Provide them explicitly or ensure azd environment is initialized."
}

# Precondition: the Container App must already exist. This script never creates one.
$existingAppJson = az containerapp show --name $ContainerAppName --resource-group $ContainerAppResourceGroup -o json 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($existingAppJson)) {
    throw "Container App '$ContainerAppName' was not found in resource group '$ContainerAppResourceGroup'. Deploy it first via 'azd up' from the root project (aks-ai-upgrade-agent/azure.yaml), then re-run this script."
}
$existingApp = $existingAppJson | ConvertFrom-Json
$containers = $existingApp.properties.template.containers
if (-not $containers -or $containers.Count -eq 0) {
    throw "Container App '$ContainerAppName' has no containers in its template; cannot determine where to set environment variables."
}
$containerName = $containers[0].name
$existingEnv = $containers[0].env
if (-not $existingEnv) { $existingEnv = @() }

Write-Host "Assigning user-assigned identity to Container App $ContainerAppName in $ContainerAppResourceGroup..."
az containerapp identity assign `
    --name $ContainerAppName `
    --resource-group $ContainerAppResourceGroup `
    --user-assigned $IdentityResourceId | Out-Null

$identityClientId = az identity show --ids $IdentityResourceId --query clientId -o tsv

# Variables this script owns. Existing values for these keys are updated in place; any other
# existing variable on the container is preserved untouched (see merge below).
$managedVars = [ordered]@{
    AZURE_CLIENT_ID = $identityClientId
}
if (-not [string]::IsNullOrWhiteSpace($AksSubscriptionId)) { $managedVars["AZURE_SUBSCRIPTION_ID"] = $AksSubscriptionId }
if (-not [string]::IsNullOrWhiteSpace($AksResourceGroup)) { $managedVars["AKS_RESOURCE_GROUP"] = $AksResourceGroup }
if (-not [string]::IsNullOrWhiteSpace($AksClusterName)) { $managedVars["AKS_CLUSTER_NAME"] = $AksClusterName }

# 'az containerapp update --set-env-vars' replaces the full list, so the complete desired
# state (existing + managed) must always be recomputed and passed back in full.
$mergedEnv = [ordered]@{}
foreach ($item in $existingEnv) {
    if ($item.secretRef) {
        $mergedEnv[$item.name] = "secretref:$($item.secretRef)"
    }
    else {
        $mergedEnv[$item.name] = $item.value
    }
}
foreach ($key in $managedVars.Keys) {
    $mergedEnv[$key] = $managedVars[$key]
}

$envArgs = foreach ($key in $mergedEnv.Keys) { "$key=$($mergedEnv[$key])" }

Write-Host "Updating environment variables on container '$containerName': preserving $($existingEnv.Count) existing, setting $($managedVars.Keys.Count) managed ($($managedVars.Keys -join ', '))."
az containerapp update `
    --name $ContainerAppName `
    --resource-group $ContainerAppResourceGroup `
    --container-name $containerName `
    --set-env-vars $envArgs | Out-Null

Write-Host "Done. AZURE_CLIENT_ID pins DefaultAzureCredential to the new user-assigned identity."

