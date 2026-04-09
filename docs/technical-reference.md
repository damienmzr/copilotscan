# Technical Reference

> Open-source Python tool to audit Microsoft 365 Copilot agents via Microsoft Graph API.
> Generates a standalone HTML report with inventory, risk indicators, and agent origin classification.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [API Endpoints Used](#api-endpoints-used)
3. [Authentication](#authentication)
4. [Data Collected](#data-collected)
5. [Risk Detection Engine](#risk-detection-engine)
6. [Known Limitations](#known-limitations)
7. [Required Permissions](#required-permissions)

---

## Architecture Overview

CopilotScan uses a **two-source hybrid approach** to maximize data coverage within what Microsoft Graph and Purview currently expose:

```
┌─────────────────────────────────────────────────────────┐
│                      CopilotScan                        │
│                                                         │
│  ┌──────────────────┐      ┌────────────────────────┐   │
│  │  GraphCollector  │      │   PurviewCollector     │   │
│  │                  │      │                        │   │
│  │ /catalog/packages│      │ /security/auditLog/    │   │
│  │ (agent inventory)│      │  queries               │   │
│  │                  │      │ (activity + inferred   │   │
│  │ Static metadata  │      │  knowledge sources)    │   │
│  └────────┬─────────┘      └───────────┬────────────┘   │
│           │                            │                 │
│           └──────────┬─────────────────┘                 │
│                      ▼                                   │
│              ┌───────────────┐                           │
│              │  RiskEngine   │                           │
│              │  (6 rules)    │                           │
│              └───────┬───────┘                           │
│                      ▼                                   │
│           ┌──────────────────────┐                       │
│           │  HTML Report Generator│                      │
│           │  (standalone, no CDN) │                      │
│           └──────────────────────┘                       │
└─────────────────────────────────────────────────────────┘
```

**Data source reliability labels used in the report:**

- ✅ `graph` — Direct from Microsoft Graph API
- 🔶 `purview-inferred` — Inferred from Purview runtime events
- ⚪ `unavailable` — Not exposed by Microsoft API at this time

---

## API Endpoints Used

### Category 1 — Agent Catalog (Admin) — Microsoft Graph Beta

| # | Method | Endpoint | Returns | Status (April 2026) |
|---|--------|----------|---------|---------------------|
| 1 | `GET` | `/beta/copilot/admin/catalog/packages` | Full agent/app inventory for the tenant | GA rollout mid-April → early May 2026 |
| 2 | `GET` | `/beta/copilot/admin/catalog/packages/{id}` | Detailed metadata + `elementDetails` (supported prompts) | GA rollout mid-April → early May 2026 |

**Supported OData filters on endpoint #1:**

```
?$filter=elementTypes/any(h:h eq 'DeclarativeAgent')
?$filter=supportedHosts/any(x:x eq 'Copilot')
?$filter=lastModifiedDateTime gt 2026-01-01T00:00:00Z
```

> ⚠️ Write methods (block/unblock/reassign) are documented but non-functional as of April 2026. CopilotScan is read-only by design.

---

### Category 2 — Usage Reports — Microsoft Graph v1.0

| # | Method | Endpoint | Returns | Status |
|---|--------|----------|---------|--------|
| 3 | `GET` | `/v1.0/reports/getMicrosoft365CopilotUsageUserDetail(period='D30')` | Per-user activity across Teams, Word, Excel, Outlook, Copilot Chat | v1.0 GA |
| 4 | `GET` | `/v1.0/reports/getMicrosoft365CopilotUserCountSummary(period='D30')` | Tenant-level counts: enabled vs active users | v1.0 GA |

> ⚠️ The beta versions of these endpoints were retired on March 31, 2026. CopilotScan uses v1.0 only.

---

### Category 3 — Purview Audit Log — Microsoft Graph Beta

| # | Method | Endpoint | Returns | Status |
|---|--------|----------|---------|--------|
| 5 | `POST` | `/beta/security/auditLog/queries` | Async audit log query — agent admin activities + Copilot interactions | Beta |
| 6 | `GET` | `/beta/security/auditLog/queries/{id}/records` | Poll results of the audit query | Beta |

**Record type filters used:**

```json
{
  "recordTypeFilters": [
    "AgentAdminActivity",
    "AgentSettingsAdminActivity",
    "CopilotInteraction"
  ]
}
```

> ⚠️ Audit query latency is unpredictable — observed between 7 minutes and 3h20 depending on Microsoft backend load. CopilotScan implements polling with a configurable timeout (default: 30 minutes).

> ⚠️ Purview admin logs cover Copilot Studio agents only. Microsoft prebuilt agents (Researcher, Analyst), third-party agents, and SharePoint agents are not included in admin activity logs.

---

## Authentication

### Default: Device Code Flow (Delegated)

Device Code Flow is the **only method that covers 100% of CopilotScan endpoints**.

```
python -m copilotscan
→ Visit https://microsoft.com/devicelogin
→ Enter code: XXXXX-XXXXX
→ Sign in with your M365 admin account
→ Token cached at ~/.copilotscan/token_cache.bin (chmod 600)
```

**Why not App Registration (client secret or certificate)?**

Both app-only methods return `424 Failed Dependency` on `GET /catalog/packages/{id}` — a server-side restriction imposed by Microsoft on admin catalog endpoints. This is not configurable or bypassable client-side.

| Endpoint | Device Code | Client Secret | Certificate |
|----------|-------------|---------------|-------------|
| `GET /catalog/packages` | ✅ | ✅ (LIST only) | ✅ (LIST only) |
| `GET /catalog/packages/{id}` | ✅ | ❌ 424 | ❌ 424 |
| `GET /reports/getCopilotUsageUserDetail` | ✅ | ✅ | ✅ |
| `GET /copilot/users/{id}/interactionHistory` | ✅ | ✅ | ✅ |
| **Total coverage** | **4/4** | **2/4** | **2/4** |

### Optional: Client Credentials (CI/CD partial mode)

Available via `--flow client_credentials` for pipelines that only need usage reports. An explicit warning is displayed when this mode is used with endpoints requiring delegated context.

### Token Cache Security

The MSAL token cache is stored at `~/.copilotscan/token_cache.bin` and is automatically created with `chmod 600`. This is the only sensitive artifact produced by CopilotScan. The refresh token expires after 90 days of inactivity (default Entra ID policy).

---

## Data Collected

### From Microsoft Graph — `/catalog/packages`

| Field | Description | Used in Report |
|-------|-------------|----------------|
| `id` | Unique agent identifier | ✅ Internal key |
| `displayName` | Agent display name | ✅ |
| `elementTypes` | Agent type (DeclarativeAgent, CustomEngineAgent, SharePointAgent…) | ✅ Origin classification |
| `type` | Package type (Microsoft, Custom, External, Shared) | ✅ |
| `isBlocked` | Whether the agent is blocked by admin | ✅ |
| `publisher` | Publisher identity (user, org, Microsoft) | ✅ Origin classification |
| `availableTo` | Scope of availability (individual, team, org) | ✅ Sharing status |
| `deployedTo` | Deployment scope | ✅ |
| `supportedHosts` | Where the agent runs (Copilot, Teams, Outlook…) | ✅ |
| `version` | Agent version | ✅ |
| `lastModifiedDateTime` | Last modification date | ✅ Inactivity detection |

**Not available via API (visible in M365 admin UI only):**

- Knowledge sources (SharePoint sites, files, connectors configured on the agent)
- Sensitivity label
- Per-agent usage metrics and billing details
- Permissions granted to the agent

### From Purview Audit Log — Inferred Knowledge Sources

When Purview data is available, CopilotScan aggregates `CopilotInteraction` events per agent to infer which SharePoint sites were most frequently accessed at runtime.

This is **runtime data, not static configuration**. It reflects what sources were actually used during user conversations, not what was configured. Labeled as `inferred` in the report.

**Coverage limitations:**

- Copilot Studio agents only (not Microsoft prebuilt or SharePoint agents)
- Requires at least E3 license for 180-day retention (E5 for 365 days)
- Data is delayed — not real-time

---

## Risk Detection Engine

See [risk-engine.md](risk-engine.md) for the full documentation of all 6 rules.

---

## Known Limitations

| Limitation | Detail | Impact on CopilotScan |
|------------|--------|----------------------|
| **Read-only API** | Write methods (block/unblock/reassign) documented but return errors | CopilotScan is reporting-only — by design |
| **App-only context** | `GET /catalog/packages/{id}` returns 424 in app-only context | Device Code Flow required as default auth |
| **403 may be feature flag** | A 403 may indicate the feature is not enabled on the tenant, not a permissions issue | Explicit error message with guidance to contact Microsoft support |
| **Knowledge sources absent** | Not exposed via Graph API | Inferred from Purview runtime events; labeled `inferred` |
| **Purview coverage gaps** | Microsoft prebuilt and SharePoint agents not in admin audit logs | `AGENT_NOT_AUDITED` flag displayed in report |
| **Purview query latency** | Audit queries take 7 min to 3h+ | Configurable polling timeout (default 30 min) |
| **Sensitivity label absent** | Not returned by `/catalog/packages` | Not included in v0.1.0 report |
| **GA rollout in progress** | Full GA expected early May 2026 | 403 errors may occur on tenants where rollout is pending |
| **E5 license for full retention** | Purview retention >180 days requires E5 | Documented in report header |

---

## Required Permissions

| Scope | Type | Used For | Minimum Role |
|-------|------|----------|-------------|
| `CopilotPackages.Read.All` | Delegated | Agent catalog inventory | AI Admin or Cloud App Admin |
| `Reports.Read.All` | Delegated | Usage reports | Reports Reader |
| `AuditLogsQuery-Entra.Read.All` | Delegated | Purview audit log queries | Compliance Administrator |

**Minimum operational role (no Global Admin required for daily use):**
`AI Admin` + `Reports Reader` + `Compliance Administrator`

**Global Admin required once:** to grant admin consent on the App Registration during initial setup.

---

## Sources

- [MC1173195 — Microsoft 365 Message Center](https://mc.merill.net/message/MC1173195) (March 2026)
- [Microsoft Learn — Copilot Admin Catalog API](https://learn.microsoft.com/en-us/graph/api/resources/copilot-admin)
- [Microsoft Learn — Copilot APIs Overview](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/copilot-apis-overview)
- [michev.info — First iteration of Agent 365 APIs](https://michev.info/blog/post/7704/first-iteration-of-agent-365-apis-now-available-on-the-graph) (April 7, 2026)
- [Microsoft Learn — Purview Audit for Copilot](https://learn.microsoft.com/en-us/purview/audit-copilot)
- [Microsoft Learn — Copilot Studio Audit in Purview](https://learn.microsoft.com/en-us/microsoft-copilot-studio/admin-logging-copilot-studio)
- [Microsoft Learn — Agent Builder vs Copilot Studio](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/copilot-studio-experience)
- Roadmap ID: 502875

---

*Last updated: April 2026 — reflects GA rollout status of Microsoft Graph Copilot Admin APIs*
