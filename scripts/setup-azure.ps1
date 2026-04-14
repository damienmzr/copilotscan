#Requires -Version 5.1
<#
.SYNOPSIS
    CopilotScan - Automated Azure App Registration setup (Windows / PowerShell)

.DESCRIPTION
    Creates an Entra ID App Registration with the minimum permissions required
    to audit Microsoft 365 Copilot agents, then writes a ready-to-use config.yaml.

    Permissions added (delegated, device-code flow):
        CopilotPackages.Read.All          - list installed Copilot agents
        Reports.Read.All                  - Copilot usage reports
        AuditLogsQuery-Entra.Read.All     - Purview audit log queries

.REQUIREMENTS
    - PowerShell 5.1 or later
    - Global Administrator account (one-time consent only)
    - Internet access (Microsoft Graph module + Entra ID)

.EXAMPLE
    .\scripts\setup-azure.ps1
    .\scripts\setup-azure.ps1 -AppName "CopilotScan-Prod" -OutputConfig ".\prod.yaml"
#>

[CmdletBinding()]
param(
    [string]$AppName      = "CopilotScan",
    [string]$OutputConfig = ".\config.yaml"
)

# NOTE: StrictMode is intentionally NOT set here — the Graph SDK v2 returns objects
# whose properties vary by call context, so strict-mode property lookups cause false errors.
$ErrorActionPreference = "Stop"

# ── Helpers ───────────────────────────────────────────────────────────────────

function Write-Step { param([string]$Msg) Write-Host "`n>  $Msg" -ForegroundColor Cyan }
function Write-OK   { param([string]$Msg) Write-Host "   OK  $Msg" -ForegroundColor Green }
function Write-Warn { param([string]$Msg) Write-Host "   !!  $Msg" -ForegroundColor Yellow }
function Write-Fail { param([string]$Msg) Write-Host "   XX  $Msg" -ForegroundColor Red }

# Safely read a property from a Graph SDK object.
# The SDK v2 sometimes puts data in .AdditionalProperties instead of direct members.
function Get-SafeProp {
    param(
        [object]$Obj,
        [string]$Name
    )
    # 1) Direct property
    try {
        $val = $Obj.$Name
        if ($null -ne $val -and $val -ne '') { return $val }
    } catch {}

    # 2) AdditionalProperties with exact casing
    try {
        $val = $Obj.AdditionalProperties[$Name]
        if ($null -ne $val -and $val -ne '') { return $val }
    } catch {}

    # 3) AdditionalProperties with camelCase
    try {
        $camel = $Name.Substring(0,1).ToLower() + $Name.Substring(1)
        $val = $Obj.AdditionalProperties[$camel]
        if ($null -ne $val -and $val -ne '') { return $val }
    } catch {}

    return $null
}

# Microsoft Graph well-known App ID (constant across all tenants)
$GraphAppId = "00000003-0000-0000-c000-000000000000"

# Delegated scopes required by CopilotScan
$RequiredScopes = @(
    @{ Name = "CopilotPackages.Read.All";      Id = "bf9fc203-c1ff-4fd4-878b-323642e462ec" },
    @{ Name = "Reports.Read.All";              Id = "02e97553-ed7b-43d0-ab3c-f8bace0d040c" },
    @{ Name = "AuditLogsQuery-Entra.Read.All"; Id = "b0afded3-3588-46d8-8b3d-9842eff778da" }
)

# ── Step 1: Ensure Microsoft.Graph module ─────────────────────────────────────

Write-Step "Checking Microsoft.Graph PowerShell module..."

if (-not (Get-Module -ListAvailable -Name Microsoft.Graph.Applications)) {
    Write-Warn "Module not found. Installing Microsoft.Graph (this may take a minute)..."
    try {
        Install-Module Microsoft.Graph -Scope CurrentUser -Force -AllowClobber -Repository PSGallery
        Write-OK "Microsoft.Graph installed."
    } catch {
        Write-Fail "Could not install Microsoft.Graph automatically."
        Write-Host ""
        Write-Host "Run this manually, then re-run the script:" -ForegroundColor Yellow
        Write-Host "    Install-Module Microsoft.Graph -Scope CurrentUser -Force" -ForegroundColor Yellow
        exit 1
    }
} else {
    Write-OK "Microsoft.Graph module found."
}

Import-Module Microsoft.Graph.Applications      -ErrorAction Stop
Import-Module Microsoft.Graph.Identity.SignIns  -ErrorAction Stop

# ── Step 2: Connect to Microsoft Graph ────────────────────────────────────────

Write-Step "Signing in to Microsoft 365 (browser window will open)..."

try {
    Connect-MgGraph `
        -Scopes "Application.ReadWrite.All","AppRoleAssignment.ReadWrite.All","DelegatedPermissionGrant.ReadWrite.All" `
        -NoWelcome
    Write-OK "Connected."
} catch {
    Write-Fail "Sign-in failed: $_"
    exit 1
}

# ── Step 3: Retrieve tenant ID ────────────────────────────────────────────────

Write-Step "Retrieving tenant information..."
$Context  = Get-MgContext
$TenantId = $Context.TenantId
Write-OK "Tenant ID: $TenantId"

# ── Step 4: Create or reuse the App Registration ──────────────────────────────

Write-Step "Looking for existing '$AppName' app registration..."

$ClientId    = $null
$AppObjectId = $null

$ExistingApps = @(Get-MgApplication -Filter "displayName eq '$AppName'" -ErrorAction SilentlyContinue)

if ($ExistingApps.Count -gt 0) {
    $App         = $ExistingApps[0]
    # FIX: use Get-SafeProp instead of direct property access
    $ClientId    = Get-SafeProp $App 'AppId'
    $AppObjectId = Get-SafeProp $App 'Id'
    Write-Warn "App '$AppName' already exists - reusing (Client ID: $ClientId)."
} else {
    Write-Step "Creating App Registration '$AppName'..."

    $RequiredResourceAccess = @(
        @{
            ResourceAppId  = $GraphAppId
            ResourceAccess = $RequiredScopes | ForEach-Object {
                @{ Id = $_.Id; Type = "Scope" }
            }
        }
    )

    # Note: -IsFallbackPublicClient is not supported as a direct parameter in all SDK versions.
    # We create the app first, then patch it via Update-MgApplication with a body hashtable.
    $App = New-MgApplication `
        -DisplayName            $AppName `
        -SignInAudience          "AzureADMyOrg" `
        -PublicClient           @{ RedirectUris = @("https://login.microsoftonline.com/common/oauth2/nativeclient") } `
        -RequiredResourceAccess $RequiredResourceAccess

    # FIX: always use Get-SafeProp - never rely on strict property access after New-Mg* calls
    $ClientId    = Get-SafeProp $App 'AppId'
    $AppObjectId = Get-SafeProp $App 'Id'

    # Fallback: if SDK still hides AppId, wait 2s and re-fetch by display name
    if (-not $ClientId) {
        Write-Warn "AppId not immediately available - waiting 2 seconds and re-fetching..."
        Start-Sleep -Seconds 2
        $Refetched = Get-MgApplication -Filter "displayName eq '$AppName'" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($Refetched) {
            $ClientId    = Get-SafeProp $Refetched 'AppId'
            $AppObjectId = Get-SafeProp $Refetched 'Id'
        }
    }

    # Enable public client flows (device code) via PATCH - more reliable than constructor param
    if ($AppObjectId) {
        try {
            Invoke-MgGraphRequest -Method PATCH `
                -Uri "https://graph.microsoft.com/v1.0/applications/$AppObjectId" `
                -Body '{"isFallbackPublicClient":true}' `
                -ContentType "application/json" | Out-Null
            Write-OK "Public client flows enabled (device-code ready)."
        } catch {
            Write-Warn "Could not enable public client flows automatically: $_"
            Write-Host "  Enable manually: portal.azure.com > App registrations > $AppName > Authentication > Allow public client flows = Yes" -ForegroundColor Yellow
        }
    }

    if ($ClientId) {
        Write-OK "App created - Client ID: $ClientId"
    } else {
        Write-Fail "Could not retrieve AppId. The app was created but ClientId is missing."
        Write-Host ""
        Write-Host "Manual fallback:" -ForegroundColor Yellow
        Write-Host "  1. Go to portal.azure.com" -ForegroundColor Yellow
        Write-Host "  2. Entra ID > App registrations > $AppName" -ForegroundColor Yellow
        Write-Host "  3. Copy the Application (client) ID and paste it below:" -ForegroundColor Yellow
        $ClientId = Read-Host "  Client ID"
        if (-not $ClientId) { exit 1 }
    }
}

# ── Step 5: Grant admin consent ───────────────────────────────────────────────

Write-Step "Granting admin consent for Microsoft Graph permissions..."

# Ensure our app has a service principal (required for OAuth2 permission grants)
$OurSpList = @(Get-MgServicePrincipal -Filter "appId eq '$ClientId'" -ErrorAction SilentlyContinue)
if ($OurSpList.Count -eq 0) {
    Write-Step "Creating service principal for '$AppName'..."
    $OurSp = New-MgServicePrincipal -AppId $ClientId
    Write-OK "Service principal created."
} else {
    $OurSp = $OurSpList[0]
}
$OurSpId = Get-SafeProp $OurSp 'Id'

# Find the Microsoft Graph service principal in this tenant
$GraphSpList = @(Get-MgServicePrincipal -Filter "appId eq '$GraphAppId'")
$GraphSp     = $GraphSpList[0]
$GraphSpId   = Get-SafeProp $GraphSp 'Id'

$ScopeString = ($RequiredScopes | ForEach-Object { $_.Name }) -join " "

try {
    $ExistingGrant = Get-MgOauth2PermissionGrant `
        -Filter "clientId eq '$OurSpId' and resourceId eq '$GraphSpId'" `
        -ErrorAction SilentlyContinue

    if ($ExistingGrant) {
        $GrantId = Get-SafeProp $ExistingGrant 'Id'
        Update-MgOauth2PermissionGrant -OAuth2PermissionGrantId $GrantId -Scope $ScopeString
        Write-OK "Admin consent updated."
    } else {
        New-MgOauth2PermissionGrant `
            -ClientId    $OurSpId `
            -ResourceId  $GraphSpId `
            -ConsentType "AllPrincipals" `
            -Scope       $ScopeString | Out-Null
        Write-OK "Admin consent granted."
    }
} catch {
    Write-Warn "Could not grant admin consent automatically: $_"
    Write-Host ""
    Write-Host "Grant consent manually:" -ForegroundColor Yellow
    Write-Host "  1. Go to portal.azure.com" -ForegroundColor Yellow
    Write-Host "  2. Entra ID > App registrations > $AppName" -ForegroundColor Yellow
    Write-Host "  3. API permissions > Grant admin consent for your organisation" -ForegroundColor Yellow
    Write-Host ""
}

# ── Step 6: Write config.yaml ─────────────────────────────────────────────────

Write-Step "Writing $OutputConfig..."

$Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm"

# FIX: here-string delimiter must be on its own line with NO leading spaces.
# Using single-quote here-string (@'...'@) avoids variable/expression issues inside.
$ConfigContent = @"
# CopilotScan configuration
# Generated by scripts/setup-azure.ps1 on $Timestamp
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
  mode: agents               # Options: agents | settings | full
  include_purview: true      # Set false to skip Purview audit log queries
  inactivity_days: 90        # Agents inactive for longer are flagged

report:
  output_path: ./copilotscan_report.html
  tenant_name: ""            # Optional: friendly name shown in the report header
"@

$ConfigContent | Out-File -FilePath $OutputConfig -Encoding utf8 -Force
Write-OK "Config written to $OutputConfig"

# ── Done ──────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "========================================================" -ForegroundColor Green
Write-Host "  CopilotScan is ready!" -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Client ID : $ClientId" -ForegroundColor White
Write-Host "  Tenant ID : $TenantId" -ForegroundColor White
Write-Host "  Config    : $OutputConfig" -ForegroundColor White
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Cyan
Write-Host "    1. pip install copilotscan  (or: pip install -e .)" -ForegroundColor White
Write-Host "    2. python -m copilotscan --config $OutputConfig" -ForegroundColor White
Write-Host ""
Write-Host "  First run opens a browser for device-code sign-in." -ForegroundColor Gray
Write-Host "  Results will be saved to copilotscan_report.html" -ForegroundColor Gray
Write-Host ""

Disconnect-MgGraph -ErrorAction SilentlyContinue