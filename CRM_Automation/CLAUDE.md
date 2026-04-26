# CRM_Automation — Rules

> Inherits from `../CLAUDE.md`.

---

## Project

- **Name:** CRM_Automation
- **Objective:** Lead scoring + routing + follow-ups + stage advancement + weekly report (hands-off)
- **Phase:** 3 — No-Code Automation Mastery (Week 7: Database, CRM & Business Logic)
- **Status:** In Progress
- **Owner:** Atlas

---

## Agentic Loop

1. **Sense:** New lead enters CRM
2. **Think:** Score 0-100 (LLM)
3. **Decide:** Route + track + stage
4. **Act:** Update CRM + email + task + notify
5. **Learn:** Conversion feedback → scoring tune

---

## Tech

| Layer | Choice |
|-------|--------|
| Language | Python |
| AI Model | euri/gpt-4o-mini |
| CRM (pluggable) | HubSpot / Zoho / Airtable |
| Email | Resend |
| Notify | Slack webhook |
| Deploy (later) | n8n cron OR GitHub Actions |

---

## Inherited Rules

All parent rules apply. Specifically:
- Tool-first (every CRM call via a tool)
- Idempotent runs (never double-process)
- Budget cap enforced per run
- Secrets in `.env` only

---

## Project-Specific Rules

- **Never mutate prod CRM during dev.** Use sandbox CRM account.
- **CRM client is abstract** — one interface, 3 backends.
- **Scoring prompt is versioned** — keep `workflows/scoring-prompt-v1.md` when v2 lands.
