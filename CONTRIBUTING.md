# Contributing to CopilotScan

Thank you for taking the time to contribute. CopilotScan is a community-maintained open-source project and every contribution — bug fixes, new risk rules, documentation improvements, or test coverage — makes it more useful for security and IT teams everywhere.

Please read this document before opening a pull request.

---

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Getting Started](#getting-started)
3. [Development Setup](#development-setup)
4. [Project Structure](#project-structure)
5. [How to Contribute](#how-to-contribute)
6. [Code Style](#code-style)
7. [Testing](#testing)
8. [Pull Request Process](#pull-request-process)
9. [Reporting Bugs](#reporting-bugs)
10. [Suggesting Features](#suggesting-features)
11. [Security Vulnerabilities](#security-vulnerabilities)

---

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). By participating you agree to uphold these standards. Instances of unacceptable behavior may be reported to the maintainers.

---

## Getting Started

Before writing code, please:

1. **Check existing issues** — your bug or idea may already be tracked.
2. **Open an issue first** for non-trivial changes (new risk rules, new collectors, breaking changes). This avoids wasted effort and ensures alignment with the project roadmap.
3. **Fork the repository** and work on a dedicated branch — never directly on `main`.

---

## Development Setup

### Requirements

- Python 3.10 or higher
- A Microsoft 365 tenant with an App Registration for live integration testing (optional but recommended for collector work)

### Install

```bash
git clone https://github.com/YOUR_ORG/copilotscan.git
cd copilotscan
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### Verify setup

```bash
ruff check src/ tests/
black --check src/ tests/
mypy src/
pytest tests/ -v
```

All four commands must pass before you open a PR.

---

## Project Structure

```
copilotscan/
├── src/
│   └── copilotscan/
│       ├── __init__.py
│       ├── __main__.py          # CLI entry point (Click)
│       ├── auth.py              # MSAL auth — Device Code + Client Credentials
│       ├── config.py            # Configuration loader (YAML + env vars)
│       ├── risk_engine.py       # 6-rule risk engine
│       ├── report.py            # Standalone HTML report generator
│       └── collectors/
│           ├── __init__.py
│           ├── graph.py         # GraphCollector — /catalog/packages
│           └── purview.py       # PurviewCollector — /security/auditLog/queries
├── tests/
│   ├── fixtures/                # Static JSON API response fixtures (anonymized)
│   ├── test_auth.py
│   ├── test_risk_engine.py
│   ├── test_collectors.py
│   └── test_report.py
├── docs/
│   ├── app-registration.md
│   ├── technical-reference.md
│   ├── risk-engine.md
│   └── configuration.md
├── examples/
│   ├── basic_scan.py
│   └── ci_partial_mode.py
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.md
│   │   └── feature_request.md
│   └── workflows/
│       └── ci.yml
├── README.md
├── CONTRIBUTING.md
├── SECURITY.md
├── LICENSE
├── pyproject.toml
└── .gitignore
```

---

## How to Contribute

### Bug fixes

1. Open a bug report issue (or confirm one already exists).
2. Branch off `main`: `git checkout -b fix/short-description`.
3. Write a failing test that reproduces the bug.
4. Fix the bug and confirm the test passes.
5. Open a pull request referencing the issue.

### New risk rules

Risk rules live in `src/copilotscan/risk_engine.py`. Each new rule must:

- Have a unique `rule_id` string constant (e.g., `"ORPHAN"`)
- Return a `RiskFlag` dataclass with fields: `rule_id`, `level`, `message_en`, `data_source`
- Be documented in `docs/risk-engine.md` with trigger conditions and rationale
- Include at least two unit tests in `tests/test_risk_engine.py`: one that fires the rule, one that confirms it does not fire when the condition is absent

### New collectors / API endpoints

Microsoft Graph APIs for Copilot are evolving rapidly. If a new endpoint becomes available:

1. Open a feature request issue with the endpoint reference and expected data.
2. Implement the collector in `src/copilotscan/collectors/`.
3. Add fixtures under `tests/fixtures/` using anonymized or fully synthetic data — **never paste real tenant data into fixtures**.
4. Update `docs/technical-reference.md` with the new endpoint, its version, and availability status.

### Documentation

Documentation improvements are always welcome. Edit files under `docs/` and open a PR — no test coverage required for docs-only changes.

---

## Code Style

CopilotScan enforces consistent style via automated tooling. All rules are configured in `pyproject.toml`.

### Formatter — Black

```bash
black src/ tests/
```

Line length: **100 characters**.

### Linter — Ruff

```bash
ruff check src/ tests/
```

Enabled rule sets: `E`, `F`, `I` (import sorting), `UP` (pyupgrade), `B` (flake8-bugbear).

### Type annotations — Mypy

All public functions and methods **must** have complete type annotations. Private helpers (prefixed with `_`) are also expected to be annotated.

```bash
mypy src/
```

### Docstrings

Use Google-style docstrings for all public classes and functions:

```python
def authenticate(self) -> AuthResult:
    """Authenticate against Microsoft Graph and return a token result.

    Tries the token cache first. Falls back to the configured auth flow
    (Device Code or Client Credentials) if the cache is empty or expired.

    Returns:
        AuthResult with access token, expiry, and account information.

    Raises:
        AuthenticationError: If the MSAL flow fails or the token response
            does not contain an access_token.
    """
```

### Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add STALE_KNOWLEDGE_SOURCE risk rule
fix: handle 403 on /catalog/packages when tenant rollout is pending
docs: update app-registration guide for Entra ID May 2026 UI changes
test: add fixtures for PurviewCollector polling timeout scenario
refactor: extract token cache logic into TokenCacheManager class
chore: bump msal to 1.29.0
```

---

## Testing

Tests live in `tests/` and use **pytest**. All tests must pass before a PR can be merged.

```bash
# Run all unit tests
pytest tests/ -v

# Run with coverage report
pytest tests/ --cov=copilotscan --cov-report=term-missing

# Run a specific module
pytest tests/test_risk_engine.py -v
```

### Conventions

- **Unit tests**: mock all external I/O (HTTP calls, filesystem writes). Use `pytest-mock` or `unittest.mock`.
- **Fixtures**: static JSON API responses go under `tests/fixtures/`. Use anonymized or fully synthetic data — **never commit real tenant data**.
- **Naming**: `test_<module>_<scenario>`, e.g. `test_risk_engine_orphan_fires_when_publisher_missing`.
- **Coverage target**: maintain at minimum **80% line coverage** on `src/copilotscan/`.

### Integration tests (opt-in)

Integration tests require real M365 credentials and are excluded from the default CI run:

```bash
export COPILOTSCAN_CLIENT_ID="your-client-id"
export COPILOTSCAN_TENANT_ID="your-tenant-id"
pytest tests/ -m integration -v
```

Integration tests are tagged with `@pytest.mark.integration`.

---

## Pull Request Process

1. **Branch naming**: `feat/short-description`, `fix/short-description`, `docs/short-description`.
2. **One concern per PR**: keep PRs focused. A PR that adds a risk rule should not also refactor the auth module.
3. **PR description** must include:
   - The issue this PR closes (`Closes #123`)
   - A summary of what changed and why
   - How to test the change manually (if applicable)
4. **Checklist before requesting review**:
   - [ ] `ruff check` passes with no errors
   - [ ] `black --check` passes
   - [ ] `mypy` passes
   - [ ] All existing tests pass
   - [ ] New tests added for new functionality
   - [ ] Documentation updated if behavior or configuration changed
5. **Review**: at least one maintainer approval is required before merge.
6. **Merge strategy**: squash merge onto `main`. The maintainer sets the final commit message.

---

## Reporting Bugs

Use the [Bug Report template](.github/ISSUE_TEMPLATE/bug_report.md).

Please include the CopilotScan version, Python version, OS, auth flow used, the exact error message, and steps to reproduce. Running with `--verbose` before filing the report often provides the most useful output.

Do **not** include real tenant IDs, client IDs, access tokens, user names, or any data from your Microsoft 365 environment.

---

## Suggesting Features

Use the [Feature Request template](.github/ISSUE_TEMPLATE/feature_request.md).

The most actionable feature requests include a concrete use case, a reference to the Microsoft Graph API endpoint that would support it (if applicable), and any known API availability constraints.

---

## Security Vulnerabilities

Please do **not** open a public GitHub issue for security vulnerabilities. See [SECURITY.md](SECURITY.md) for the responsible disclosure process.
