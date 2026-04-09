# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x (current) | ✅ Security fixes backported |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

To report a vulnerability, email the maintainers at **security@YOUR_ORG** with:

- A description of the vulnerability and its potential impact
- Steps to reproduce or a minimal proof-of-concept
- Any suggested mitigations (optional)

You will receive an acknowledgment within **48 hours**. We aim to publish a fix within **14 days** for critical issues and **30 days** for moderate issues, depending on severity and complexity.

We will credit reporters in the release notes unless you request otherwise.

## Scope

CopilotScan is a **read-only** tool. It does not store, transmit, or modify tenant data. The only artifacts it produces are:

- The generated HTML report (written locally, never transmitted)
- The MSAL token cache at `~/.copilotscan/token_cache.bin` (created with `chmod 600`)

**Treat the HTML report as confidential** — it contains your tenant's agent inventory and should not be shared externally without review.

## Known Non-Issues

The following are by design and are not considered vulnerabilities:

- The HTML report embeds agent metadata from your tenant (see above — treat it accordingly)
- The token cache stores a refresh token that expires after 90 days of inactivity (default Entra ID policy)
- CopilotScan uses Microsoft Graph beta endpoints — these may change without notice
