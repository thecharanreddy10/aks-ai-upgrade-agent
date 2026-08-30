param(
    [string]$Endpoint = "https://aks-mcp.happyriver-781373bd.eastus2.azurecontainerapps.io/mcp",
    [string]$SubscriptionId = "bb0e2c9e-d7fb-45e4-92cf-654f380e6388",
    [string]$ResourceGroup = "AKS_Upgrade_Agent",
    [string]$ClusterName = "AKS_oldapp_cluster",
    [string]$Namespace = "default",
    [int]$TimeoutSec = 300
)

$ErrorActionPreference = 'Stop'

function Invoke-Mcp {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Method,

        [Parameter(Mandatory = $false)]
        [hashtable]$Params = @{},

        [Parameter(Mandatory = $true)]
        [string]$Id
    )

    $payload = @{
        jsonrpc = "2.0"
        id      = $Id
        method  = $Method
        params  = $Params
    } | ConvertTo-Json -Depth 20

    try {
        $http = Invoke-WebRequest -Uri $Endpoint -Method Post -ContentType "application/json" -Body $payload -TimeoutSec $TimeoutSec -UseBasicParsing
        $response = $http.Content | ConvertFrom-Json
    }
    catch {
        if ($_.Exception.Response) {
            $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            $raw = $reader.ReadToEnd()
            if (-not [string]::IsNullOrWhiteSpace($raw)) {
                try {
                    $response = $raw | ConvertFrom-Json
                }
                catch {
                    throw "HTTP request failed for method '$Method' (id=$Id): $raw"
                }
            }
            else {
                throw "HTTP request failed for method '$Method' (id=$Id) with empty response body."
            }
        }
        else {
            throw
        }
    }

    if ($null -ne $response.error) {
        $errText = $response.error | ConvertTo-Json -Depth 20 -Compress
        throw "MCP error for method '$Method' (id=$Id): $errText"
    }

    return $response
}

function Invoke-Tool {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ToolName,

        [Parameter(Mandatory = $true)]
        [hashtable]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$Id
    )

    $response = Invoke-Mcp -Method "tools/call" -Params @{ name = $ToolName; arguments = $Arguments } -Id $Id

    if ($null -eq $response.result -or $null -eq $response.result.content) {
        throw "Tool '$ToolName' returned no content"
    }

    $contentItems = @($response.result.content)
    if ($contentItems.Count -eq 0) {
        throw "Tool '$ToolName' returned empty content array"
    }

    $text = $contentItems[0].text
    if ([string]::IsNullOrWhiteSpace($text)) {
        throw "Tool '$ToolName' returned empty text"
    }

    try {
        $parsed = $text | ConvertFrom-Json
    }
    catch {
        throw "Tool '$ToolName' returned non-JSON text"
    }

    return $parsed
}

Write-Host "Validating MCP endpoint: $Endpoint"

$checks = @()

try {
    $listResponse = Invoke-Mcp -Method "tools/list" -Id "list-1"
    $toolNames = @($listResponse.result.tools | ForEach-Object { $_.name })

    $requiredTools = @(
        "aks_get_cluster_details",
        "aks_get_node_pools",
        "aks_get_available_upgrades",
        "aks_check_node_health",
        "aks_check_pod_health",
        "aks_check_pdb"
    )

    foreach ($required in $requiredTools) {
        $present = $toolNames -contains $required
        $checks += [PSCustomObject]@{
            Check   = "tools/list contains $required"
            Status  = if ($present) { "PASS" } else { "FAIL" }
            Details = if ($present) { "found" } else { "missing" }
        }
    }
}
catch {
    $checks += [PSCustomObject]@{
        Check   = "tools/list"
        Status  = "FAIL"
        Details = $_.Exception.Message
    }
}

$baseArgs = @{
    subscription_id = $SubscriptionId
    resource_group  = $ResourceGroup
    cluster_name    = $ClusterName
}

$toolCalls = @(
    @{ name = "aks_get_cluster_details"; args = $baseArgs; id = "call-1" },
    @{ name = "aks_get_node_pools"; args = $baseArgs; id = "call-2" },
    @{ name = "aks_get_available_upgrades"; args = $baseArgs; id = "call-3" },
    @{ name = "aks_check_node_health"; args = $baseArgs; id = "call-4" },
    @{ name = "aks_check_pod_health"; args = (@{} + $baseArgs + @{ namespace = $Namespace }); id = "call-5" },
    @{ name = "aks_check_pdb"; args = (@{} + $baseArgs + @{ namespace = $Namespace }); id = "call-6" }
)

foreach ($call in $toolCalls) {
    try {
        $result = Invoke-Tool -ToolName $call.name -Arguments $call.args -Id $call.id
        $checks += [PSCustomObject]@{
            Check   = "tools/call $($call.name)"
            Status  = "PASS"
            Details = ($result | ConvertTo-Json -Depth 6 -Compress)
        }
    }
    catch {
        $checks += [PSCustomObject]@{
            Check   = "tools/call $($call.name)"
            Status  = "FAIL"
            Details = $_.Exception.Message
        }
    }
}

$checks | Format-Table -AutoSize

$failed = @($checks | Where-Object { $_.Status -eq "FAIL" })
if ($failed.Count -gt 0) {
    Write-Error "Validation failed: $($failed.Count) check(s) failed."
    exit 1
}

Write-Host "All MCP checks passed."
exit 0
