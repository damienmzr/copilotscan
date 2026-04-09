---
name: Bug Report
about: Something is not working as expected
title: "[BUG] "
labels: ["bug", "needs-triage"]
assignees: []
---

## Bug Description

<!-- A clear and concise description of what the bug is. -->

## Steps to Reproduce

<!-- Minimal steps to reproduce the behavior. -->

1. Configure CopilotScan with `...`
2. Run `python -m copilotscan ...`
3. Observe the error

## Expected Behavior

<!-- What did you expect to happen? -->

## Actual Behavior

<!-- What actually happened? Paste the full error output below. -->

```
<paste error output here>
```

## Environment

| Field | Value |
|-------|-------|
| CopilotScan version | <!-- run: python -m copilotscan --version --> |
| Python version | <!-- run: python --version --> |
| Operating System | <!-- e.g. macOS 14, Windows 11, Ubuntu 24.04 --> |
| Auth flow used | <!-- device_code / client_credentials --> |
| Purview enabled | <!-- yes / no (--no-purview flag used?) --> |
| M365 license tier | <!-- E3 / E5 / other --> |

## Verbose Log Output

<!-- Re-run with --verbose and paste the relevant lines. Remove all sensitive data first. -->

<details>
<summary>Full --verbose output</summary>

```
<paste here>
```

</details>

## Microsoft Graph API Response (if applicable)

<!-- If the bug involves an API error, paste the HTTP status code and response body.
     IMPORTANT: Remove tenant IDs, client IDs, user names, and any PII before posting. -->

```json
{
  "error": {
    "code": "...",
    "message": "..."
  }
}
```

## Additional Context

<!-- Anything else relevant: tenant region, whether the Copilot Admin Catalog feature
     is enabled in your tenant, E3 vs E5 Purview retention, GA rollout status, etc. -->

---

> **Security reminder** — Do not paste tenant IDs, client IDs, access tokens, user names,
> agent names, or any data from your Microsoft 365 environment into this issue.
