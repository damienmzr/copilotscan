# Configuration Reference

CopilotScan reads configuration from a YAML file (default: `./config.yaml`) and from environment variables. **Environment variables always take precedence** over values in config.yaml.

---

## config.yaml

```yaml
# ── Authentication ────────────────────────────────────────────────────────────

# Directory (tenant) ID from your Entra ID App Registration overview page.
# Required.
tenant_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

# Application (client) ID from your Entra ID App Registration overview page.
# Required.
client_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

# Authentication flow.
# "device_code"          — Delegated, interactive. Full API coverage. Recommended.
# "client_credentials"   — App-only. Partial coverage (424 on /catalog/packages/{id}).
# Default: device_code
auth_flow: "device_code"

# Client secret. Required only when auth_flow is "client_credentials".
# Prefer COPILOTSCAN_CLIENT_SECRET env var over storing secrets in config.yaml.
# client_secret: ""

# Path to the MSAL token cache file.
# Default: ~/.copilotscan/token_cache.bin  (created with chmod 600)
# cache_path: "~/.copilotscan/token_cache.bin"

# ── Risk Engine ───────────────────────────────────────────────────────────────

# Number of days of inactivity before the INACTIVE risk rule fires.
# Set to 0 to disable the INACTIVE rule.
# Default: 90
inactivity_threshold_days: 90

# ── Purview Collector ─────────────────────────────────────────────────────────

# Maximum time (minutes) to wait for a Purview audit query to complete.
# Purview queries are async and latency is highly variable (observed: 7 min to 3h+).
# Default: 30
purview_poll_timeout_minutes: 30

# ── Output ────────────────────────────────────────────────────────────────────

# Default output path for the HTML report.
# Can be overridden with --output on the CLI.
# Default: ./report.html
# output: "report.html"
```

---

## Environment Variables

Environment variables override the corresponding config.yaml values.

| Variable | Overrides | Description |
|----------|-----------|-------------|
| `COPILOTSCAN_TENANT_ID` | `tenant_id` | Directory (tenant) ID |
| `COPILOTSCAN_CLIENT_ID` | `client_id` | Application (client) ID |
| `COPILOTSCAN_CLIENT_SECRET` | `client_secret` | Client secret (client_credentials flow only) |
| `COPILOTSCAN_CACHE_PATH` | `cache_path` | Token cache file path |

**Example:**

```bash
export COPILOTSCAN_TENANT_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export COPILOTSCAN_CLIENT_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
python -m copilotscan --output report.html
```

---

## CLI Flags

All flags override both config.yaml and environment variables.

| Flag | Default | Description |
|------|---------|-------------|
| `--config PATH` | `./config.yaml` | Path to configuration file |
| `--output PATH` | `./report.html` | Output HTML report path |
| `--flow TEXT` | `device_code` | Auth flow: `device_code` or `client_credentials` |
| `--no-purview` | off | Skip Purview audit log collection entirely |
| `--inactivity-days INT` | `90` | Override `inactivity_threshold_days` |
| `--timeout-minutes INT` | `30` | Override `purview_poll_timeout_minutes` |
| `--verbose` | off | Enable DEBUG-level logging |
| `--version` | — | Print version and exit |
| `--help` | — | Show help and exit |

---

## Precedence Order

```
CLI flags  >  Environment variables  >  config.yaml  >  Built-in defaults
```

---

## Minimal Configuration (Device Code Flow)

```yaml
tenant_id: "your-tenant-id"
client_id: "your-client-id"
```

## Minimal Configuration (CI/CD — Client Credentials)

```bash
# .env (excluded from git via .gitignore)
COPILOTSCAN_TENANT_ID=your-tenant-id
COPILOTSCAN_CLIENT_ID=your-client-id
COPILOTSCAN_CLIENT_SECRET=your-client-secret
```

```bash
python -m copilotscan --flow client_credentials --no-purview --output report.html
```

> **Note:** Client Credentials flow skips `GET /catalog/packages/{id}` (returns 424).
> The report will include a warning banner and reduced agent detail.
