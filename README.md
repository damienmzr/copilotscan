# CopilotScan

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Microsoft Graph](https://img.shields.io/badge/Microsoft%20Graph-Beta%20%2B%20v1.0-0078D4?logo=microsoft)](https://learn.microsoft.com/en-us/graph/overview)
[![Status: Beta](https://img.shields.io/badge/status-beta-orange.svg)]()
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

**Open-source Python tool to audit Microsoft 365 Copilot agents via the Microsoft Graph API.**

Generates a self-contained HTML report with agent inventory, risk classification, and knowledge-source inference — no data ever leaves your machine.

[**Quickstart**](#quickstart) · [**Documentation**](docs/) · [**Report a Bug**](https://github.com/YOUR_ORG/copilotscan/issues/new?template=bug_report.md) · [**Request a Feature**](https://github.com/YOUR_ORG/copilotscan/issues/new?template=feature_request.md)

</div>

---

## Why CopilotScan?

Any licensed Microsoft 365 user can build a Copilot agent grounded on SharePoint data and share it org-wide — **without IT approval**. This is the primary shadow-AI risk vector in M365 tenants today.

CopilotScan gives security and IT teams a clear, scriptable inventory of every agent deployed in the tenant, scored against 6 automated risk rules.

---

## Features

- **Complete agent inventory** via the Graph Admin Catalog API (`/beta/copilot/admin/catalog/packages`)
- **Knowledge-source inference** — aggregates Purview audit events to surface which SharePoint sites each agent accessed at runtime
- **6-rule risk engine** — flags orphaned agents, inactive agents, shadow-AI from Agent Builder, sensitive knowledge sources, and more
- **Standalone HTML report** — single file, no CDN, no external requests, safe to share internally
- **Read-only by design** — CopilotScan never modifies tenant configuration
- **Token cache** — MSAL token persisted at `~/.copilotscan/token_cache.bin` (chmod 600), no repeated sign-ins

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      CopilotScan                        │
│                                                         │
│  ┌──────────────────┐      ┌────────────────────────┐   │
│  │  GraphCollector  │      │   PurviewCollector     │   │
│  │ /catalog/packages│      │ /security/auditLog/    │   │
│  │ (agent inventory)│      │  queries               │   │
│  │ Static metadata  │      │ (activity + inferred   │   │
│  │                  │      │  knowledge sources)    │   │
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
│           │  HTML Report         │                       │
│           │  (standalone, no CDN)│                       │
│           └──────────────────────┘                       │
└─────────────────────────────────────────────────────────┘
```

Data source reliability labels used in the report:

- ✅ `graph` — Direct from Microsoft Graph API
- 🔶 `purview-inferred` — Inferred from Purview runtime events
- ⚪ `unavailable` — Not exposed by Microsoft APIs at this time

---

## Requirements

### Microsoft 365 Licence

> ⚠️ **The agent catalog endpoint requires a Microsoft 365 Copilot enterprise licence.**
>
> The `/beta/copilot/admin/catalog/packages` endpoint is **not available** with:
> - Microsoft 365 Copilot for Business (SMB/PME version)
> - Microsoft 365 Business Basic / Standard / Premium without Copilot add-on
>
> **Required:** Microsoft 365 E3 or E5 **+** Microsoft 365 Copilot add-on licence (enterprise).
>
> If your tenant returns `HTTP 403`, verify your licence in the Microsoft 365 admin center
> under **Billing > Licences**. If the licence is correct, the endpoint GA rollout
> (expected early May 2026) may not yet be active on your tenant — retry after May 1, 2026.

---

## Quickstart

### Prerequisites

| Requirement | Details |
|-------------|---------|
| Python | 3.10 or higher |
| M365 account | Roles: **AI Admin** + **Reports Reader** + **Compliance Administrator** |
| App Registration | Single-tenant, public client flow enabled (see [setup guide](docs/app-registration.md)) |
| Global Admin | Required **once only** to grant admin consent |

### 1 — Install

> **Licence required:** Microsoft 365 Copilot (enterprise — E3/E5 + Copilot add-on).
> M365 Copilot for Business is not supported by the agent catalog API.

```bash
pip install copilotscan
```

Or from source:

```bash
git clone https://github.com/YOUR_ORG/copilotscan.git
cd copilotscan
pip install -e ".[dev]"
```

### 2 — Configure

```yaml
# config.yaml
tenant_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
client_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
auth_flow: "device_code"          # recommended — full API coverage
inactivity_threshold_days: 90
purview_poll_timeout_minutes: 30
```

Or use environment variables (takes precedence over config.yaml):

```bash
export COPILOTSCAN_TENANT_ID="your-tenant-id"
export COPILOTSCAN_CLIENT_ID="your-client-id"
```

### 3 — Run

```bash
python -m copilotscan --output report.html
```

CopilotScan prompts you to authenticate via Device Code Flow:

```
────────────────────────────────────────────────────────────
  🔐  CopilotScan — Authentication required
────────────────────────────────────────────────────────────
  1. Open:       https://microsoft.com/devicelogin
  2. Enter code: XXXXX-XXXXX
  3. Sign in with an account holding the role:
       AI Admin  +  Reports Reader
  ℹ  Code valid for 15 minutes
────────────────────────────────────────────────────────────
```

The report is generated at `./report.html`. Open it in any browser — no internet connection required.

### CLI Reference

```
Usage: python -m copilotscan [OPTIONS]

Options:
  --config PATH         Path to config.yaml  [default: ./config.yaml]
  --output PATH         Output HTML report path  [default: ./report.html]
  --flow TEXT           Auth flow: device_code | client_credentials  [default: device_code]
  --no-purview          Skip Purview audit log collection
  --inactivity-days INT Inactivity threshold in days  [default: 90]
  --timeout-minutes INT Purview polling timeout  [default: 30]
  --verbose             Enable debug logging
  --version             Show version and exit.
  --help                Show this message and exit.
```

---

## Risk Engine

CopilotScan applies 6 automated risk rules to every agent:

| Rule | Level | Trigger |
|------|-------|---------|
| `INACTIVE` | 🟠 MEDIUM | No activity for more than N days (default: 90) |
| `ORPHAN` | 🔴 HIGH | Shared agent with no identifiable owner |
| `SENSITIVE_KNOWLEDGE_SOURCE` | 🔴 HIGH | Runtime knowledge source accessible by Everyone or externals |
| `KNOWLEDGE_SOURCE_UNKNOWN` | 🟡 LOW | No Purview data available — cannot assess knowledge sources |
| `AGENT_NOT_AUDITED` | ℹ️ INFO | Microsoft prebuilt or SharePoint agent — no audit trail |
| `ORIGIN_RISK` | varies | Agent Builder → HIGH · Copilot Studio → MEDIUM · Microsoft prebuilt → INFO |

Full rule rationale and configuration options: [docs/risk-engine.md](docs/risk-engine.md)

---

## Required Permissions

| Scope | Type | Purpose | Minimum Role |
|-------|------|---------|--------------|
| `CopilotPackages.Read.All` | Delegated | Agent catalog inventory | AI Admin or Cloud App Admin |
| `Reports.Read.All` | Delegated | Usage reports | Reports Reader |
| `AuditLogsQuery-Entra.Read.All` | Delegated | Purview audit log queries | Compliance Administrator |

> **Why Device Code Flow?**
> `GET /catalog/packages/{id}` returns `424 Failed Dependency` under app-only auth — a server-side
> Microsoft restriction. Device Code Flow is the only method with full API coverage.

Full setup: [docs/app-registration.md](docs/app-registration.md)

---

## Known Limitations

| Limitation | Impact |
|------------|--------|
| Write API methods non-functional as of April 2026 | CopilotScan is reporting-only — by design |
| `GET /catalog/packages/{id}` returns 424 in app-only context | Device Code Flow required |
| Knowledge sources not exposed via Graph API | Inferred from Purview events; labeled `inferred` |
| Purview covers Copilot Studio agents only | Microsoft prebuilt and SharePoint agents show `AGENT_NOT_AUDITED` |
| Purview query latency: 7 min to 3h+ | Configurable polling timeout (default: 30 min) |
| GA rollout in progress (full GA expected early May 2026) | 403 errors possible on some tenants |
| Microsoft 365 Copilot for Business (SMB) not supported | The `/catalog/packages` endpoint requires the enterprise Copilot licence (E3/E5 + add-on). M365 Copilot for Business returns `403 Forbidden`. |

Full reference: [docs/technical-reference.md](docs/technical-reference.md)

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/app-registration.md](docs/app-registration.md) | Step-by-step Entra ID App Registration setup |
| [docs/technical-reference.md](docs/technical-reference.md) | API endpoints, data model, authentication flows |
| [docs/risk-engine.md](docs/risk-engine.md) | All 6 risk rules with rationale and configuration |
| [docs/configuration.md](docs/configuration.md) | All config.yaml keys and environment variables |
| [examples/](examples/) | Usage examples and CI/CD integration snippets |

---

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

Quick summary: use `black` + `ruff`, add type annotations on all public functions, include tests for new risk rules and collectors, and reference an issue in your PR description.

---

## Security

CopilotScan is read-only and produces no output other than the HTML report and the MSAL token cache at `~/.copilotscan/token_cache.bin` (created with chmod 600).

To report a security vulnerability, please do **not** open a public issue — see [SECURITY.md](SECURITY.md) for responsible disclosure instructions.

---

## License

[MIT](LICENSE) — © 2026 CopilotScan Contributors
