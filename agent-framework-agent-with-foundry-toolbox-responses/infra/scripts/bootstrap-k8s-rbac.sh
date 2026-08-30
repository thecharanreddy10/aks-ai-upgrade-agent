#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 ]]; then
  echo "Usage: $0 <subscription_id> <aks_resource_group> <aks_cluster_name> <subject_name> [subject_kind] [service_account_namespace]"
  exit 1
fi

SUBSCRIPTION_ID="$1"
AKS_RESOURCE_GROUP="$2"
AKS_CLUSTER_NAME="$3"
SUBJECT_NAME="$4"
SUBJECT_KIND="${5:-User}"
SERVICE_ACCOUNT_NAMESPACE="${6:-default}"
CLUSTER_ROLE_NAME="aks-operations-readonly"
CLUSTER_ROLE_BINDING_NAME="aks-operations-readonly-binding"

if [[ "$SUBJECT_KIND" != "User" && "$SUBJECT_KIND" != "Group" && "$SUBJECT_KIND" != "ServiceAccount" ]]; then
  echo "subject_kind must be one of: User, Group, ServiceAccount"
  exit 1
fi

echo "Setting Azure subscription context..."
az account set --subscription "$SUBSCRIPTION_ID" >/dev/null

echo "Fetching AKS kubeconfig for cluster $AKS_CLUSTER_NAME..."
az aks get-credentials --resource-group "$AKS_RESOURCE_GROUP" --name "$AKS_CLUSTER_NAME" --overwrite-existing >/dev/null

SUBJECT_BLOCK="- kind: ${SUBJECT_KIND}
  name: ${SUBJECT_NAME}"

if [[ "$SUBJECT_KIND" == "ServiceAccount" ]]; then
  SUBJECT_BLOCK="- kind: ServiceAccount
  name: ${SUBJECT_NAME}
  namespace: ${SERVICE_ACCOUNT_NAMESPACE}"
else
  SUBJECT_BLOCK="${SUBJECT_BLOCK}
  apiGroup: rbac.authorization.k8s.io"
fi

cat > /tmp/aks-k8s-rbac-bootstrap.yaml <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: ${CLUSTER_ROLE_NAME}
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
  name: ${CLUSTER_ROLE_BINDING_NAME}
subjects:
${SUBJECT_BLOCK}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: ${CLUSTER_ROLE_NAME}
EOF

echo "Applying Kubernetes RBAC manifest..."
if command -v kubectl >/dev/null 2>&1; then
  kubectl apply -f /tmp/aks-k8s-rbac-bootstrap.yaml
else
  echo "kubectl not found locally. Falling back to 'az aks command invoke'."
  az aks command invoke \
    --resource-group "$AKS_RESOURCE_GROUP" \
    --name "$AKS_CLUSTER_NAME" \
    --command "kubectl apply -f aks-k8s-rbac-bootstrap.yaml" \
    --file /tmp/aks-k8s-rbac-bootstrap.yaml >/dev/null
fi

echo "Kubernetes RBAC bootstrap completed."
echo "Subject kind: ${SUBJECT_KIND}"
echo "Subject name: ${SUBJECT_NAME}"
