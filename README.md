# CopilotScan

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Microsoft Graph](https://img.shields.io/badge/Microsoft%20Graph-Beta%20%2B%20v1.0-0078D4?logo=microsoft)](https://learn.microsoft.com/en-us/graph/overview)
[![Status: Beta](https://img.shields.io/badge/status-beta-orange.svg)]()
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

**Open-source Python tool to audit Microsoft 365 Copilot agents via the Microsoft Graph API.**

Generates a self-contained HTML report with agent inventory, risk classification, manifest data (knowledge sources, instructions, capabilities) and Purview-observed usage — no data ever leaves your machine.

[**Quickstart**](#quickstart) · [**Documentation**](docs/) · [**Report a Bug**](https://github.com/YOUR_ORG/copilotscan/issues/new?template=bug_report.md) · [**Request a Feature**](https://github.com/YOUR_ORG/copilotscan/issues/new?template=feature_request.md)

</div>

---

## Why CopilotScan?

Any licensed Microsoft 365 user can build a Copilot agent grounded on SharePoint data and share it org-wide — **without IT approval**. This is the primary shadow-AI risk vector in M365 tenants today.

CopilotScan gives security and IT teams a clear, scriptable inventory of every agent deployed in the tenant, scored against automated risk rules.

---

## Features

- **Complete agent inventory** — only real Copilot agents, filtered by `supportedHosts` to match exactly what the Microsoft 365 admin center shows
- **Manifest enrichment** — fetches each agent's declared data sources (WebSearch, OneDrive/SharePoint, Graph Connectors), system instructions, conversation starters and plugin actions directly from the Graph detail endpoint
- **Creator UPN resolution** — resolves the publisher `userId` to a `userPrincipalName` via the Graph Users API (with in-memory cache)
- **Origin classification** (7 categories):
  | Origin | Description |
  |--------|-------------|
  | `microsoft_prebuilt` | First-party Microsoft agents |
  | `third_party` | ISV / marketplace agents (`thirdParty` type) |
  | `sharepoint_agent` | Agents built in SharePoint |
  | `agent_builder` | Declarative agents built with Agent Builder |
  | `copilot_studio` | Declarative agents built with Copilot Studio (org-scoped) |
  | `pro_code` | Custom engine / pro-code agents |
  | `unknown` | Unclassified |
- **Purview audit integration** — aggregates Purview audit events to surface which knowledge sources each agent *actually* accessed at runtime ("Sources observées")
- **Risk engine** — automated risk rules covering inactivity, orphaned agents, shadow-AI origin, and unknown knowledge sources
- **Interactive HTML report** — single standalone file, filterable by origin, risk, scope and capabilities (with checkbox dropdown), CSV export
- **Read-only by design** — CopilotScan never modifies tenant configuration
- **Token cache** — MSAL token persisted at `~/.copilotscan/token_cache.bin` (chmod 600)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        CopilotScan                          │
│                                                             │
│  ┌──────────────────────┐      ┌────────────────────────┐   │
│  │   GraphCollector     │      │   PurviewCollector     │   │
│  │ /catalog/packages    │      │ /security/auditLog/    │   │
│  │  → inventory +       │      │   queries              │   │
│  │    supportedHosts    │      │ (observed knowledge    │   │
│  │    filter            │      │  sources)              │   │
│  │ /catalog/packages/id │      └───────────┬────────────┘   │
│  │  → manifest data     │                  │                │
│  │    (capabilities,    │                  │                │
│  │    instructions,     │                  │                │
│  │    actions)          │                  │                │
│  │ /v1.0/users/{id}     │                  │                │
│  │  → creator UPN       │                  │                │
│  └──────────┬───────────┘                  │                │
│             └──────────────┬───────────────┘                │
│                            ▼                                │
│                   ┌───────────────┐                         │
│                   │  RiskEngine   │                         │
│                   └───────┬───────┘                         │
│                           ▼                                 │
│              ┌──────────────────────┐                       │
│              │  HTML Report         │                       │
│              │  (standalone, no CDN)│                       │
│              └──────────────────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

Data source reliability labels used in the report:

- ✅ `graph` — Direct from Microsoft Graph API
- 🔶 `purview-inferred` — Inferred from Purview runtime events
- ⚪ `unavailable` — Not exposed by Microsoft APIs at this time

---

## Requirements

### Microsoft 365 Licence

> ℹ️ **CopilotScan fonctionne avec Microsoft 365 Copilot for Business (PME) et Microsoft 365 Copilot Enterprise (E3/E5 + add-on).**
>
> Si votre tenant retourne `HTTP 403`, vérifiez que votre compte dispose des rôles requis (AI Admin ou Cloud App Admin) et que le consentement administrateur a été accordé sur l’app registration.

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

> **Licence requise :** Microsoft 365 Copilot (Business ou Enterprise).

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

## Report

Each agent row expands to show a detail panel containing:

- **Metadata** — Platform, Creator (UPN), Version, Last Modified, Last Purview Activity, ID
- **Risk Flags** — rule ID, severity badge, data source, message
- **Sources observées (Purview)** — knowledge sources the agent actually accessed during conversations, as recorded in the Purview audit log. Empty means no activity was logged in the scanned period — not that the agent has no configured sources.
- **Instructions** — system prompt declared in the agent manifest
- **Conversation Starters** — suggested prompts from the manifest
- **Actions** — plugin/connector actions
- **Data Sources** — sources declared in the manifest: WebSearch (with target sites), OneDrive/SharePoint (with file/folder URLs or SharePoint IDs), Graph Connectors
- **Agent Tools** — capabilities like image generation (GraphicArt) and Code Interpreter

### Filters

The filter bar supports:
- Free-text search (name, publisher, type)
- Origin, Risk Level, Scope dropdowns
- **Capabilities** dropdown with checkboxes (OR logic — shows agents with any selected capability), split into *Data Sources* and *Agent Skills*
- **↺ Réinitialiser** button to clear all filters at once

---

## Risk Engine

CopilotScan applies automated risk rules to every agent:

| Rule | Level | Trigger |
|------|-------|---------|
| `INACTIVE` | 🟠 MEDIUM | No activity for more than N days (default: 90) |
| `ORPHAN` | 🔴 HIGH | Shared agent with no identifiable owner |
| `SENSITIVE_KNOWLEDGE_SOURCE` | 🔴 HIGH | Runtime knowledge source accessible by Everyone or externals |
| `KNOWLEDGE_SOURCE_UNKNOWN` | 🟡 LOW | No Purview data available — cannot assess knowledge sources |
| `AGENT_NOT_AUDITED` | ℹ️ INFO | Microsoft prebuilt or SharePoint agent — no audit trail |
| `ORIGIN_RISK` | varies | Agent Builder / SharePoint Agent → HIGH · Copilot Studio → MEDIUM · Third Party → LOW · Microsoft prebuilt → INFO |

Full rule rationale and configuration options: [docs/risk-engine.md](docs/risk-engine.md)

---

## Required Permissions

| Scope | Type | Purpose | Minimum Role |
|-------|------|---------|--------------|
| `CopilotPackages.Read.All` | Delegated | Agent catalog inventory + manifest data | AI Admin or Cloud App Admin |
| `User.Read` | Delegated | Resolve agent creator `userId` → `userPrincipalName` | — (standard user permission) |
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
| Manifest data only available for tenant-built (`shared`) agents | Microsoft Prebuilt and Third-Party agents do not expose `elementDetails` |
| Knowledge sources not exposed via Graph API | Inferred from Purview events; labeled `inferred` |
| Purview covers Copilot Studio agents only | Microsoft prebuilt and SharePoint agents show `AGENT_NOT_AUDITED` |
| Purview query latency: 7 min to 3h+ | Configurable polling timeout (default: 30 min) |
| GA rollout in progress (full GA expected early May 2026) | 403 errors possible on some tenants |
| ~~Microsoft 365 Copilot for Business (SMB) not supported~~ | **Testé et fonctionnel avec M365 Copilot for Business.** |

Full reference: [docs/technical-reference.md](docs/technical-reference.md)

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/app-registration.md](docs/app-registration.md) | Step-by-step Entra ID App Registration setup |
| [docs/technical-reference.md](docs/technical-reference.md) | API endpoints, data model, authentication flows |
| [docs/risk-engine.md](docs/risk-engine.md) | Risk rules with rationale and configuration |
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
