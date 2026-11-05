#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

: "${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID in .env or the environment}"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-a2a-server-bootcamp}"
LOCATION="${AZURE_LOCATION:-westeurope}"
APP_NAME="${AZURE_CONTAINER_APP:-a2a-server-bootcamp}"

echo "==> Preflight"
az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az account show --query "{subscription:name, user:user.name}" --output table
for namespace in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry; do
    state=$(az provider show --namespace "$namespace" --query registrationState --output tsv)
    if [ "$state" != "Registered" ]; then
        echo "$namespace is $state, register it before deploying" >&2
        exit 1
    fi
done

env_vars=("LOG_LEVEL=${LOG_LEVEL:-INFO}")
if [ -n "${AGENT_NAME:-}" ]; then env_vars+=("AGENT_NAME=$AGENT_NAME"); fi
if [ -n "${AGENT_DESCRIPTION:-}" ]; then env_vars+=("AGENT_DESCRIPTION=$AGENT_DESCRIPTION"); fi
if [ -n "${PUBLIC_BASE_URL:-}" ]; then env_vars+=("PUBLIC_BASE_URL=$PUBLIC_BASE_URL"); fi

echo "==> Build and deploy $APP_NAME to $RESOURCE_GROUP ($LOCATION)"
az containerapp up \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --source . \
    --ingress external \
    --target-port 2009 \
    --env-vars "${env_vars[@]}"

az containerapp update \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --min-replicas 1 \
    --max-replicas 1 \
    --output none

fqdn=$(az containerapp show \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query properties.configuration.ingress.fqdn \
    --output tsv)
echo "==> Agent card: https://$fqdn/.well-known/agent-card.json"
