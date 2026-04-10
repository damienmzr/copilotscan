#!/usr/bin/env bash
# =============================================================================
# CopilotScan — Automated Azure App Registration setup (bash / Azure CLI)
# =============================================================================
#
# Creates an Entra ID App Registration with the minimum permissions required
# to audit Microsoft 365 Copilot agents, then writes a ready-to-use config.yaml.
#
# Permissions added (delegated, device-code flow):
#   CopilotPackages.Read.All          — list installed Copilot agents
#   Reports.Read.All                  — Copilot usage reports
#   AuditLogsQuery-Entra.Read.All     — Purview audit log queries
#
# Requirements:
#   • Azure CLI  (brew install azure-cli  |  apt install azure-cli  |  winget install Microsoft.AzureCLI)
#   • Global Administrator account (one-time consent only)
#
# Usage:
#   chmod +x scripts/setup-azure.sh
#   ./scripts/setup-azure.sh
#   ./scripts/setup-azure.sh --app-name "CopilotScan-Prod" --output ./prod.yaml
# =============================================================================

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
APP_NAME="CopilotScan"
OUTPUT_CONFIG="./config.yaml"

# Microsoft Graph well-known App ID
GRAPH_APP_ID="00000003-0000-0000-c000-000000000000"

# Required delegated scopes (name:GUID pairs)
declare -A SCOPES=(
    ["CopilotPackages.Read.All"]="bf9fc203-c1ff-4fd4-878b-323642e462ec"
    ["Reports.Read.All"]="02e97553-ed7b-43d0-ab3c-f8bace0d040c"
    ["AuditLogsQuery-Entra.Read.All"]="b0afded3-3588-46d8-8b3d-9842eff778da"
)

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --app-name)   APP_NAME="$2";     shift 2 ;;
        --output)     OUTPUT_CONFIG="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,30p' "$0" | grep '^#' | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'

step() { echo -e "\n${CYAN}▶  $1${RESET}"; }
ok()   { echo -e "   ${GREEN}✅  $1${RESET}"; }
warn() { echo -e "   ${YELLOW}⚠️   $1${RESET}"; }
fail() { echo -e "   ${RED}❌  $1${RESET}"; }

# ── Step 1 — Check Azure CLI ──────────────────────────────────────────────────
step "Checking Azure CLI…"

if ! command -v az &>/dev/null; then
    fail "Azure CLI not found."
    cat <<'EOF'

Install it for your OS, then re-run this script:
  macOS  :  brew install azure-cli
  Ubuntu :  curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
  Windows:  winget install Microsoft.AzureCLI
            (or run scripts/setup-azure.ps1 instead)

EOF
    exit 1
fi

AZ_VERSION=$(az version --query '"azure-cli"' -o tsv 2>/dev/null || echo "unknown")
ok "Azure CLI ${AZ_VERSION} found."

# ── Step 2 — Sign in ─────────────────────────────────────────────────────────
step "Signing in to Azure (browser or device-code login)…"

if ! az account show &>/dev/null; then
    az login
fi
ok "Signed in."

# ── Step 3 — Get tenant ID ────────────────────────────────────────────────────
step "Retrieving tenant information…"
TENANT_ID=$(az account show --query tenantId -o tsv)
ok "Tenant ID: ${TENANT_ID}"

# ── Step 4 — Create or reuse App Registration ─────────────────────────────────
step "Looking for existing '${APP_NAME}' app registration…"

EXISTING_CLIENT_ID=$(az ad app list --display-name "${APP_NAME}" --query "[0].appId" -o tsv 2>/dev/null || true)

if [[ -n "${EXISTING_CLIENT_ID}" && "${EXISTING_CLIENT_ID}" != "None" ]]; then
    warn "App '${APP_NAME}' already exists — reusing (Client ID: ${EXISTING_CLIENT_ID})."
    CLIENT_ID="${EXISTING_CLIENT_ID}"
else
    step "Creating App Registration '${APP_NAME}'…"

    CLIENT_ID=$(az ad app create \
        --display-name "${APP_NAME}" \
        --sign-in-audience AzureADMyOrg \
        --enable-id-token-issuance false \
        --enable-access-token-issuance false \
        --public-client-redirect-uris "https://login.microsoftonline.com/common/oauth2/nativeclient" \
        --query appId -o tsv)

    # Enable public-client / fallback flows
    az ad app update \
        --id "${CLIENT_ID}" \
        --set publicClient.redirectUris='["https://login.microsoftonline.com/common/oauth2/nativeclient"]' \
        --is-fallback-public-client true

    ok "App created — Client ID: ${CLIENT_ID}"
fi

# ── Step 5 — Add API permissions ──────────────────────────────────────────────
step "Adding Microsoft Graph delegated permissions…"

for SCOPE_NAME in "${!SCOPES[@]}"; do
    SCOPE_ID="${SCOPES[$SCOPE_NAME]}"
    az ad app permission add \
        --id "${CLIENT_ID}" \
        --api "${GRAPH_APP_ID}" \
        --api-permissions "${SCOPE_ID}=Scope" 2>/dev/null || true
    ok "  ${SCOPE_NAME}"
done

# ── Step 6 — Grant admin consent ──────────────────────────────────────────────
step "Granting admin consent (requires Global Admin)…"

if az ad app permission admin-consent --id "${CLIENT_ID}" 2>/dev/null; then
    ok "Admin consent granted."
else
    warn "Could not grant admin consent automatically."
    cat <<EOF

Grant consent manually in Azure Portal:
  1. https://portal.azure.com → Entra ID → App Registrations → ${APP_NAME}
  2. API permissions → Grant admin consent for your organisation
  3. Re-run this script or proceed with the config.yaml created below.

EOF
fi

# ── Step 7 — Write config.yaml ────────────────────────────────────────────────
step "Writing ${OUTPUT_CONFIG}…"

TIMESTAMP=$(date "+%Y-%m-%d %H:%M")

cat > "${OUTPUT_CONFIG}" <<EOF
# CopilotScan configuration
# Generated by scripts/setup-azure.sh on ${TIMESTAMP}
#
# Run the scanner:
#   pip install copilotscan
#   python -m copilotscan --config ${OUTPUT_CONFIG}

auth:
  mode: device-code          # Options: device-code | app-secret | app-cert
  tenant_id: "${TENANT_ID}"
  client_id: "${CLIENT_ID}"
  # client_secret: ""        # Uncomment for app-secret mode
  # cert_path: ""            # Uncomment for app-cert mode
  # cert_thumb: ""           # Uncomment for app-cert mode

scan:
  include_purview: true      # Set false to skip Purview audit log queries
  inactivity_days: 90        # Agents inactive for longer are flagged

report:
  output_path: ./copilotscan_report.html
  tenant_name: ""            # Optional: friendly name shown in the report header
EOF

ok "Config written to ${OUTPUT_CONFIG}"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}  CopilotScan is ready!${RESET}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
cat <<EOF

  Client ID  : ${CLIENT_ID}
  Tenant ID  : ${TENANT_ID}
  Config     : ${OUTPUT_CONFIG}

  Next steps:
    1. pip install copilotscan          (or: pip install -e .)
    2. python -m copilotscan --config ${OUTPUT_CONFIG}

  First run opens a browser for device-code sign-in.
  Results will be saved to copilotscan_report.html

EOF
