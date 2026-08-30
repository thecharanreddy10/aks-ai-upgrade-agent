#!/usr/bin/env bash
set -euo pipefail

FUNCTION_APP_NAME="${1:-}"
RESOURCE_GROUP="${2:-}"
PROJECT_ROOT="${3:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

resolve_azd_value() {
  local key="$1"
  local value
  value="$(azd env get-value "$key" 2>/dev/null || true)"
  echo "${value%\"}" | sed 's/^"//'
}

if [[ -z "$FUNCTION_APP_NAME" ]]; then
  FUNCTION_APP_NAME="$(resolve_azd_value AKS_MCP_FUNCTION_APP_NAME)"
fi
if [[ -z "$FUNCTION_APP_NAME" ]]; then
  FUNCTION_APP_NAME="$(resolve_azd_value aksMcpFunctionAppNameOut)"
fi
if [[ -z "$RESOURCE_GROUP" ]]; then
  RESOURCE_GROUP="$(resolve_azd_value AZURE_RESOURCE_GROUP)"
fi

if [[ -z "$FUNCTION_APP_NAME" || -z "$RESOURCE_GROUP" ]]; then
  echo "Unable to resolve Function App name or resource group. Pass args: <function_app_name> <resource_group>."
  exit 1
fi

SRC_FOLDER="$PROJECT_ROOT/src/aks-operations-mcp"
if [[ ! -d "$SRC_FOLDER" ]]; then
  echo "AKS MCP source folder not found: $SRC_FOLDER"
  exit 1
fi

TEMP_ROOT="/tmp/aks-mcp-function-package"
ZIP_PATH="/tmp/aks-mcp-function.zip"

rm -rf "$TEMP_ROOT" "$ZIP_PATH"
mkdir -p "$TEMP_ROOT"
cp -R "$SRC_FOLDER"/* "$TEMP_ROOT"/
find "$TEMP_ROOT" -type d -name "__pycache__" -prune -exec rm -rf {} +

(
  cd "$TEMP_ROOT"
  zip -r "$ZIP_PATH" . >/dev/null
)

echo "Deploying AKS MCP package to Function App $FUNCTION_APP_NAME in $RESOURCE_GROUP..."
az functionapp deployment source config-zip --name "$FUNCTION_APP_NAME" --resource-group "$RESOURCE_GROUP" --src "$ZIP_PATH" >/dev/null

echo "Deployment completed."
echo "Function endpoint: https://$FUNCTION_APP_NAME.azurewebsites.net/api/mcp"
