# Risk Engine

CopilotScan applies **6 risk rules** to every agent in the tenant inventory. Rules are evaluated after all data collection is complete and run against the merged Graph + Purview dataset.

Every `RiskFlag` produced by the engine includes:

| Field | Type | Description |
|-------|------|-------------|
| `rule_id` | str | Rule identifier (e.g. `"ORPHAN"`) |
| `level` | str | `HIGH`, `MEDIUM`, `LOW`, or `INFO` |
| `message_en` | str | Human-readable description in English |
| `data_source` | str | `graph`, `purview`, `purview-inferred`, or `unavailable` |

An agent may carry **multiple risk flags** simultaneously.

---

## Rule 1 — INACTIVE

**Level:** 🟠 MEDIUM
**Data source:** `purview`

**Trigger:** No `AgentAdminActivity` or `CopilotInteraction` event recorded for this agent within the past N days (configurable via `inactivity_threshold_days`, default: 90).

**Rationale:** Inactive agents with shared or org-wide access represent unnecessary attack surface. They may be abandoned, no longer maintained, and their knowledge sources may have drifted to include sensitive data added after the agent was last reviewed.

**Configuration:**

```yaml
inactivity_threshold_days: 90   # default; set to 0 to disable this rule
```

**Note:** This rule requires Purview audit data. If `--no-purview` is used or no Purview data is available for an agent, this rule does not fire. Agents that have never had any Purview events are flagged by `KNOWLEDGE_SOURCE_UNKNOWN` instead.

---

## Rule 2 — ORPHAN

**Level:** 🔴 HIGH
**Data source:** `graph`

**Trigger:** The agent is shared beyond individual scope (`availableTo` ≠ `individual`) **and** has no identifiable owner (`publisher` field is anonymous, missing, or an internal system identifier with no resolvable account).

**Rationale:** No accountable owner means no one to review, update, respond to incidents, or remove the agent when it is no longer needed. Shared orphaned agents are the highest-risk class of shadow-AI artifacts.

**No configuration required** — this rule runs on all agents from Graph data alone.

---

## Rule 3 — SENSITIVE_KNOWLEDGE_SOURCE *(inferred)*

**Level:** 🔴 HIGH
**Data source:** `purview-inferred`

**Trigger:** Purview `CopilotInteraction` events are available for this agent **and** the most frequently accessed runtime knowledge source (SharePoint site) has been identified as broadly accessible — `Everyone` sharing, external guest access, or anonymous link sharing detected.

**Rationale:** The agent may be exposing sensitive SharePoint content to any user who prompts it, regardless of whether those users have direct permissions on the underlying site.

**Important caveats:**
- This is runtime data, not static configuration. It reflects what sources were actually accessed during user conversations, not what was explicitly configured in the agent's knowledge settings.
- Labeled `inferred` in the report to clearly distinguish it from direct Graph data.
- Coverage is limited to Copilot Studio agents (see Rule 5).

---

## Rule 4 — KNOWLEDGE_SOURCE_UNKNOWN

**Level:** 🟡 LOW (informational)
**Data source:** `unavailable`

**Trigger:** No Purview `CopilotInteraction` data is available for this agent — either because Purview data collection was skipped (`--no-purview`), the Purview query timed out, or the agent has genuinely had no user interactions in the audit window.

**Rationale:** When knowledge sources cannot be assessed, the admin should verify manually that the agent is not grounded on sensitive data. This flag is intentionally LOW severity — it signals a data gap, not a confirmed risk.

---

## Rule 5 — AGENT_NOT_AUDITED

**Level:** ℹ️ INFO
**Data source:** `graph`

**Trigger:** The agent is classified as a **Microsoft prebuilt agent** (Researcher, Analyst, etc.) or a **SharePoint agent** — both of which are excluded from Purview admin activity logs by Microsoft.

**Rationale:** No audit trail is currently available for these agent types via the Microsoft Graph Purview APIs. This is a platform limitation, not a CopilotScan limitation. Admins should be aware that these agents cannot be assessed for knowledge source or activity risks at this time.

---

## Rule 6 — ORIGIN_RISK

**Level:** varies
**Data source:** `graph`

**Trigger:** Classification of the agent's creation origin based on the combination of `elementTypes`, `publisher`, and `availableTo` fields from the Graph catalog.

| Origin Class | Condition | Risk Level |
|--------------|-----------|------------|
| `AGENT_BUILDER` | `DeclarativeAgent` type + end-user publisher + shared beyond individual | 🔴 HIGH |
| `SHAREPOINT_AGENT` | `elementTypes` contains `SharePointAgent` | 🔴 HIGH |
| `COPILOT_STUDIO` | `DeclarativeAgent` type + org/maker publisher | 🟠 MEDIUM |
| `PRO_CODE` | `CustomEngineAgent` type | 🟠 MEDIUM |
| `MICROSOFT_PREBUILT` | `publisher` = Microsoft | ℹ️ INFO |
| `UNKNOWN` | Cannot be classified from available fields | 🟡 LOW |

**Why Agent Builder is HIGH risk:**
Any user with a Microsoft 365 license can create an agent in Copilot using Agent Builder (formerly "Copilot Studio in Teams") and share it with the entire organization. No IT approval is required, no review process exists, and the agent can be grounded on any SharePoint site the creator has access to — including sites with broad permissions. This is the primary shadow-AI vector in M365 tenants today.

**Why SharePoint agents are HIGH risk:**
SharePoint agents are automatically created from document libraries and can be shared by any site owner. They inherit the sharing permissions of the underlying library, which may be broader than intended, and they are not audited in Purview admin logs.

**No configuration required** — this rule runs on all agents using Graph data alone, with no Purview dependency. It provides risk classification even in `--no-purview` mode.

---

## Rule Evaluation Order and Multiple Flags

Rules are evaluated independently — an agent can carry flags from multiple rules simultaneously. For example, a shared Agent Builder agent with no Purview data would receive both `ORIGIN_RISK (HIGH)` and `KNOWLEDGE_SOURCE_UNKNOWN (LOW)`.

The report sorts agents by their **highest severity flag** in descending order: HIGH → MEDIUM → LOW → INFO → none.

---

## Adding a New Rule

See [CONTRIBUTING.md](../CONTRIBUTING.md#new-risk-rules) for the full contribution guide. In summary:

1. Define a unique `rule_id` constant in `risk_engine.py`
2. Implement an evaluation function returning a `RiskFlag | None`
3. Register it in the main `evaluate()` dispatcher
4. Document it here in this file
5. Add unit tests for both the firing and non-firing cases
