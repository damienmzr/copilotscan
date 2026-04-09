# App Registration Setup

This guide walks through the one-time setup required to run CopilotScan against your Microsoft 365 tenant.

## Requirements

- Access to the [Microsoft Entra ID Portal](https://entra.microsoft.com)
- **Global Administrator** role (required once to grant admin consent)

After initial setup, day-to-day use only requires: **AI Admin** + **Reports Reader** + **Compliance Administrator**.

---

## Step 1 — Create the App Registration

1. Go to **Entra ID Portal** → **App registrations** → **New registration**
2. Set the following values:
   - **Name**: `CopilotScan`
   - **Supported account types**: Accounts in this organizational directory only (Single tenant)
   - **Redirect URI**: leave empty
3. Click **Register**

Copy the **Application (client) ID** and **Directory (tenant) ID** from the overview page — you will need both values for `config.yaml`.

---

## Step 2 — Enable Public Client Flow

1. In the app → **Authentication**
2. Under **Advanced settings**, set **Allow public client flows** → **Yes**
3. Click **Save**

This setting is required for Device Code Flow, which is the only authentication method that provides full API coverage. See [Authentication](technical-reference.md#authentication) for the full comparison.

---

## Step 3 — Add Delegated Permissions

1. In the app → **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions**
2. Search for and select each of the following scopes:

| Scope | Purpose |
|-------|---------|
| `CopilotPackages.Read.All` | Agent catalog inventory |
| `Reports.Read.All` | Copilot usage reports |
| `AuditLogsQuery-Entra.Read.All` | Purview audit log queries |

3. Click **Add permissions**

---

## Step 4 — Grant Admin Consent

1. On the **API permissions** page, click **Grant admin consent for [your tenant]**
2. Confirm — this step requires **Global Administrator**
3. All three permissions should show status: **Granted for [tenant]** ✅

This step is required **once only**. After this, any user with the minimum operational roles can run CopilotScan without Global Admin involvement.

---

## Step 5 — Configure CopilotScan

Create a `config.yaml` file in your working directory:

```yaml
# config.yaml
tenant_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"   # Directory (tenant) ID
client_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"   # Application (client) ID
auth_flow: "device_code"
inactivity_threshold_days: 90
purview_poll_timeout_minutes: 30
```

Or set environment variables (these take precedence over config.yaml):

```bash
export COPILOTSCAN_TENANT_ID="your-tenant-id"
export COPILOTSCAN_CLIENT_ID="your-client-id"
```

---

## Step 6 — Run

```bash
pip install copilotscan
python -m copilotscan --output report.html
```

---

## Troubleshooting

**403 Forbidden on `/catalog/packages`**

The Copilot Admin Catalog API is in GA rollout (mid-April → early May 2026). A 403 response may indicate the feature is not yet enabled on your tenant — this is a feature flag issue, not a permissions issue. Contact Microsoft Support and reference Roadmap ID 502875.

**424 Failed Dependency on `/catalog/packages/{id}`**

You are using app-only auth (Client Credentials flow). This is a server-side Microsoft restriction that cannot be bypassed client-side. Switch to Device Code Flow (`auth_flow: "device_code"`) for full API coverage.

**403 on `/security/auditLog/queries`**

Verify the `AuditLogsQuery-Entra.Read.All` scope is granted and the account has the **Compliance Administrator** role. Purview audit access requires at minimum an E3 license.

**Token cache permission error**

CopilotScan creates `~/.copilotscan/token_cache.bin` with `chmod 600`. If you see a permissions error, check that the directory `~/.copilotscan/` is writable. You can override the path with `COPILOTSCAN_CACHE_PATH`.
