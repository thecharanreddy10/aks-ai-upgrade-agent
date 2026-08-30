param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter(Mandatory = $true)]
    [string]$AksResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$AksClusterName,

    [Parameter(Mandatory = $true)]
    [string]$SubjectName,

    [ValidateSet('User', 'Group', 'ServiceAccount')]
    [string]$SubjectKind = 'User',

    [string]$ServiceAccountNamespace = 'default',

    [string]$ClusterRoleName = 'aks-operations-readonly',

    [string]$ClusterRoleBindingName = 'aks-operations-readonly-binding'
)

$ErrorActionPreference = 'Stop'

Write-Host "Setting Azure subscription context..."
az account set --subscription $SubscriptionId | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Failed to set Azure subscription context."
}

Write-Host "Fetching AKS kubeconfig for cluster $AksClusterName..."
az aks get-credentials --resource-group $AksResourceGroup --name $AksClusterName --overwrite-existing | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Failed to fetch AKS kubeconfig."
}

$subjectBlock = @"
- kind: $SubjectKind
  name: $SubjectName
"@

if ($SubjectKind -eq 'ServiceAccount') {
    $subjectBlock = @"
- kind: ServiceAccount
  name: $SubjectName
  namespace: $ServiceAccountNamespace
"@
}
else {
    $subjectBlock += "`n  apiGroup: rbac.authorization.k8s.io"
}

$manifest = @"
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: $ClusterRoleName
rules:
- apiGroups: [""]
  resources: ["nodes", "pods", "pods/status", "namespaces"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["policy"]
  resources: ["poddisruptionbudgets"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: $ClusterRoleBindingName
subjects:
$subjectBlock
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: $ClusterRoleName
"@

$tempFile = Join-Path $env:TEMP "aks-k8s-rbac-bootstrap.yaml"
$manifest | Set-Content -Path $tempFile -Encoding UTF8

Write-Host "Applying Kubernetes RBAC manifest..."
$kubectl = Get-Command kubectl -ErrorAction SilentlyContinue
if ($kubectl) {
  kubectl apply -f $tempFile
  if ($LASTEXITCODE -ne 0) {
    throw "kubectl apply failed."
  }
}
else {
  Write-Host "kubectl not found locally. Falling back to 'az aks command invoke'."
  az aks command invoke `
    --resource-group $AksResourceGroup `
    --name $AksClusterName `
    --command "kubectl apply -f aks-k8s-rbac-bootstrap.yaml" `
    --file $tempFile | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "az aks command invoke failed. Ensure the cluster is running and you have permission."
  }
}

Write-Host "Kubernetes RBAC bootstrap completed."
Write-Host "Subject kind: $SubjectKind"
Write-Host "Subject name: $SubjectName"
