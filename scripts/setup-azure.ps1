#Requires -Version 5.1
<#
.SYNOPSIS
    CopilotScan — Automated Azure App Registration setup (Windows / PowerShell)

.DESCRIPTION
    Creates an Entra ID App Registration with the minimum permissions required
    to audit Microsoft 365 Copilot agents, then writes a ready-to-use config.yaml.

    Permissions added (delegated, device-code flow):
        CopilotPackages.Read.All          — list installed Copilot agents
        Reports.Read.All                  — Copilot usage reports
        AuditLogsQuery-Entra.Read.All     — Purview audit log queries

.REQUIREMENTS
    • PowerShell 5.1 or later
    • Global Administrator account (one-time consent only)
    • Internet access (Microsoft Graph module + Entra ID)

.EXAMPLE
    .\scripts\setup-azure.ps1
    .\scripts\setup-azure.ps1 -AppName "CopilotScan-Prod" -OutputConfig ".\prod.yaml"
#>

[CmdletBinding()]
param(
    [string]$AppName    = "CopilotScan",
    [string]$OutputConfig = ".\config.yaml"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Helpers ──────────────────────────────────────────────────────────────────

function Write-Step  { param([string]$Msg) Write-Host "`n▶  $Msg" -ForegroundColor Cyan }
function Write-OK    { param([string]$Msg) Write-Host "   ✅  $Msg" -ForegroundColor Green }
function Write-Warn  { param([string]$Msg) Write-Host "   ⚠️   $Msg" -ForegroundColor Yellow }
function Write-Fail  { param([string]$Msg) Write-Host "   ❌  $Msg" -ForegroundColor Red }

# Microsoft Graph App ID (well-known constant — never changes)
$GraphAppId = "00000003-0000-0000-c000-000000000000"

# Scopes required by CopilotScan (delegated)
$RequiredScopes = @(
    @{ Name = "CopilotPackages.Read.All";      Id = "bf9fc203-c1ff-4fd4-878b-323642e462ec" },
    @{ Name = "Reports.Read.All";              Id = "02e97553-ed7b-43d0-ab3c-f8bace0d040c" },
    @{ Name = "AuditLogsQuery-Entra.Read.All"; Id = "b0afded3-3588-46d8-8b3d-9842eff778da" }
)

# ── Step 1 — Ensure Microsoft.Graph module ───────────────────────────────────

Write-Step "Checking Microsoft.Graph PowerShell module…"

if (-not (Get-Module -ListAvailable -Name Microsoft.Graph.Applications)) {
    Write-Warn "Module not found. Installing Microsoft.Graph (this may take a minute)…"
    try {
        Install-Module Microsoft.Graph -Scope CurrentUser -Force -AllowClobber -Repository PSGallery
        Write-OK "Microsoft.Graph installed."
    }
    catch {
        Write-Fail "Could not install Microsoft.Graph automatically."
        Write-Host @"

Run this manually, then re-run the script:
    Install-Module Microsoft.Graph -Scope CurrentUser -Force

"@ -ForegroundColor Yellow
        exit 1
    }
}
else {
    Write-OK "Microsoft.Graph module found."
}

Import-Module Microsoft.Graph.Applications -ErrorAction Stop
Import-Module Microsoft.Graph.Identity.SignIns -ErrorAction Stop

# ── Step 2 — Connect to Microsoft Graph ──────────────────────────────────────

Write-Step "Signing in to Microsoft 365 (browser window will open)…"

try {
    Connect-MgGraph `
        -Scopes "Application.ReadWrite.All", "AppRoleAssignment.ReadWrite.All", "DelegatedPermissionGrant.ReadWrite.All" `
        -NoWelcome
    Write-OK "Connected."
}
catch {
    Write-Fail "Sign-in failed: $_"
    exit 1
}

# ── Step 3 — Retrieve tenant info ────────────────────────────────────────────

Write-Step "Retrieving tenant information…"
$Context  = Get-MgContext
$TenantId = $Context.TenantId
Write-OK "Tenant ID: $TenantId"

# ── Step 4 — Create or reuse App Registration ────────────────────────────────

Write-Step "Looking for existing '$AppName' app registration…"

$ExistingApps = Get-MgApplication -Filter "displayName eq '$AppName'" -ErrorAction SilentlyContinue
$App = $ExistingApps | Select-Object -First 1

if ($App) {
    Write-Warn "App '$AppName' already exists — reusing (Client ID: $($App.AppId))."
}
else {
    Write-Step "Creating App Registration '$AppName'…"

    $RequiredResourceAccess = @(
        @{
            ResourceAppId  = $GraphAppId
            ResourceAccess = $RequiredScopes | ForEach-Object {
                @{ Id = $_.Id; Type = "Scope" }
            }
        }
    )

    $App = New-MgApplication -DisplayName $AppName `
        -SignInAudience "AzureADMyOrg" `
        -IsFallbackPublicClient $true `
        -PublicClient @{ RedirectUris = @("https://login.microsoftonline.com/common/oauth2/nativeclient") } `
        -RequiredResourceAccess $RequiredResourceAccess

    Write-OK "App created — Client ID: $($App.AppId)"
}

$ClientId = $App.AppId

# ── Step 5 — Grant admin consent ─────────────────────────────────────────────

Write-Step "Granting admin consent for Microsoft Graph permissions…"

# We need the service principal of our app (create if first run)
$OurSp = Get-MgServicePrincipal -Filter "appId eq '$ClientId'" -ErrorAction SilentlyContinue
if (-not $OurSp) {
    $OurSp = New-MgServicePrincipal -AppId $ClientId
    Write-OK "Service principal created."
}

# Find the Microsoft Graph service principal in this tenant
$GraphSp = Get-MgServicePrincipal -Filter "appId eq '$GraphAppId'"

try {
    # Check if a grant already exists
    $ExistingGrant = Get-MgOauth2PermissionGrant -Filter "clientId eq '$($OurSp.Id)' and resourceId eq '$($GraphSp.Id)'" -ErrorAction SilentlyContinue

    $ScopeString = ($RequiredScopes | ForEach-Object { $_.Name }) -join " "

    if ($ExistingGrant) {
        Update-MgOauth2PermissionGrant -OAuth2PermissionGrantId $ExistingGrant.Id `
            -Scope $ScopeString
        Write-OK "Admin consent updated."
    }
    else {
        New-MgOauth2PermissionGrant `
            -ClientId    $OurSp.Id `
            -ResourceId  $GraphSp.Id `
            -ConsentType "AllPrincipals" `
            -Scope       $ScopeString | Out-Null
        Write-OK "Admin consent granted."
    }
}
catch {
    Write-Warn "Could not grant admin consent automatically: $_"
    Write-Host @"

Grant consent manually in Azure Portal:
  1. Go to https://portal.azure.com → Entra ID → App Registrations → $AppName
  2. API permissions → Grant admin consent for your organisation
  3. Re-run this script or proceed with the config.yaml created below.

"@ -ForegroundColor Yellow
}

# ── Step 6 — Write config.yaml ────────────────────────────────────────────────

Write-Step "Writing $OutputConfig…"

$ConfigContent = @"
# CopilotScan configuration
# Generated by scripts/setup-azure.ps1 on $(Get-Date -Format "yyyy-MM-dd HH:mm")
#
# Run the scanner:
#   pip install copilotscan
#   python -m copilotscan --config $OutputConfig

auth:
  mode: device-code          # Options: device-code | app-secret | app-cert
  tenant_id: "$TenantId"
  client_id: "$ClientId"
  # client_secret: ""        # Uncomment for app-secret mode
  # cert_path: ""            # Uncomment for app-cert mode
  # cert_thumb: ""           # Uncomment for app-cert mode

scan:
  include_purview: true      # Set false to skip Purview audit log queries
  inactivity_days: 90        # Agents inactive for longer are flagged

report:
  output_path: ./copilotscan_report.html
  tenant_name: ""            # Optional: friendly name shown in the report header
"@

$ConfigContent | Out-File -FilePath $OutputConfig -Encoding utf8 -Force
Write-OK "Config written to $OutputConfig"

# ── Done ─────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host "  CopilotScan is ready!" -ForegroundColor Green
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host @"

  Client ID  : $ClientId
  Tenant ID  : $TenantId
  Config     : $OutputConfig

  Next steps:
    1. pip install copilotscan          (or: pip install -e .)
    2. python -m copilotscan --config $OutputConfig

  First run opens a browser for device-code sign-in.
  Results will be saved to copilotscan_report.html

"@ -ForegroundColor White

Disconnect-MgGraph -ErrorAction SilentlyContinue
