param(
    [string]$FunctionAppName = "",
    [string]$ResourceGroup = "",
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

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

if ([string]::IsNullOrWhiteSpace($FunctionAppName)) {
    $FunctionAppName = Get-AzdValue -Key "AKS_MCP_FUNCTION_APP_NAME"
    if ([string]::IsNullOrWhiteSpace($FunctionAppName)) {
        $FunctionAppName = Get-AzdValue -Key "aksMcpFunctionAppNameOut"
    }
}

if ([string]::IsNullOrWhiteSpace($ResourceGroup)) {
    $ResourceGroup = Get-AzdValue -Key "AZURE_RESOURCE_GROUP"
}

if ([string]::IsNullOrWhiteSpace($FunctionAppName) -or [string]::IsNullOrWhiteSpace($ResourceGroup)) {
    throw "Unable to resolve Function App name or resource group. Provide -FunctionAppName and -ResourceGroup, or ensure azd environment is initialized."
}

$srcFolder = Join-Path $ProjectRoot "src/aks-operations-mcp"
if (-not (Test-Path $srcFolder)) {
    throw "AKS MCP source folder not found: $srcFolder"
}

$tempRoot = Join-Path $env:TEMP "aks-mcp-function-package"
$zipPath = Join-Path $env:TEMP "aks-mcp-function.zip"

if (Test-Path $tempRoot) {
    Remove-Item $tempRoot -Recurse -Force
}
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

New-Item -ItemType Directory -Path $tempRoot | Out-Null
Copy-Item (Join-Path $srcFolder "*") -Destination $tempRoot -Recurse -Force

$pycache = Get-ChildItem -Path $tempRoot -Filter "__pycache__" -Directory -Recurse -ErrorAction SilentlyContinue
if ($pycache) {
    $pycache | Remove-Item -Recurse -Force
}

Compress-Archive -Path (Join-Path $tempRoot "*") -DestinationPath $zipPath -Force

Write-Host "Deploying AKS MCP package to Function App $FunctionAppName in $ResourceGroup..."
az functionapp deployment source config-zip --name $FunctionAppName --resource-group $ResourceGroup --src $zipPath | Out-Null

Write-Host "Deployment completed."
Write-Host "Function endpoint: https://$FunctionAppName.azurewebsites.net/api/mcp"
