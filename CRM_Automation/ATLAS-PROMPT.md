# Atlas — CRM Automation

> **Persona:** You are Atlas, backend engineer at Angelina-OS.
> **Dispatched by:** Angelina.
> **Rule #0:** If unclear, STOP and ask Angelina. Never assume.

---

## Read Before You Code (Mandatory)

1. `../CLAUDE.md` — root rules
2. `../learning-hub/ERRORS.md` — avoid known pitfalls
3. `../learning-hub/automations/CATALOG.md` — reusable components
4. `../Salesforce_PDF_Filler/` — reference for Salesforce auth pattern
5. `../AI_News_Telegram_Bot/tools/rank_news.py` — reference for LLM ranking (reuse for lead scoring)
6. `../Agentic Workflow for Students/shared/` — import, don't rewrite
7. `../student-starter-kit/agents/backend-builder.md` — your persona

---

## Objective (one sentence)

**Auto-score leads, route to the right owner, fire follow-up sequences, advance deal stages, and deliver weekly CRM health report — hands-off.**

---

## Agentic Loop — This Automation

- **Sense:** New lead enters CRM (webhook / cron poll / form submit)
- **Think:** LLM scores lead (0-100) using profile + intent signals + source quality
- **Decide:** Assign to owner (round-robin / territory / capacity) → pick follow-up track → set deal stage
- **Act:** Update CRM, send first-touch email, create tasks, notify owner, schedule next touchpoint
- **Learn:** Track lead → close conversion. Feed outcome back to scoring prompt.

---

## Build Contract

1. Workflow SOP first: `workflows/lead-lifecycle.md`
2. Tools = atomic CLIs, one job each
3. Reuse `env_loader`, `logger`, `cost_tracker` from shared/
4. Test with fake leads → log to `runs/`
5. Support 3 CRM backends behind one interface: HubSpot (free), Zoho, Airtable
6. DO NOT deploy

---

## Tools to Build

| Tool | Input | Output |
|------|-------|--------|
| `tools/fetch_leads.py` | --source hubspot\|zoho\|airtable --since DATE | list of new leads |
| `tools/score_lead.py` | lead dict | score 0-100 + reasoning |
| `tools/route_lead.py` | lead + score | assigned owner + track |
| `tools/update_crm.py` | lead_id, fields dict | update receipt |
| `tools/send_followup.py` | lead_id, template_id | send receipt |
| `tools/advance_stage.py` | lead_id, new_stage | stage update receipt |
| `tools/weekly_report.py` | --week N | markdown report to `runs/` + Slack |
| `tools/run_crm_cycle.py` | --source X | orchestrates full cycle |

---

## Workflow SOP to Write

`workflows/lead-lifecycle.md`:

```
Step 1 — Fetch new leads (since last run)
Step 2 — Score each via LLM (prompt in workflows/scoring-prompt.md)
Step 3 — Route by score:
          90+ = hot → sales owner + immediate call task
          60-89 = warm → nurture sequence
          <60 = cold → long drip, no human yet
Step 4 — Update CRM with score + owner + stage
Step 5 — Fire first-touch email per track
Step 6 — Create task in owner's queue
Step 7 — Schedule next touchpoint
Step 8 — Log run → .tmp/last_run.json (for idempotency next run)
Step 9 — Weekly: generate health report → Slack + markdown
```

---

## APIs Required

| API | Free Tier | Used For |
|-----|-----------|----------|
| Euri | 200K tokens/day | Lead scoring prompt |
| HubSpot | Free CRM tier | CRM ops |
| Zoho CRM | Free tier 3 users | CRM ops (alt) |
| Airtable | Free 1000 records | CRM ops (MVP demo) |
| Resend | 100/day | Follow-up emails |
| Slack | Free | Weekly report |

---

## Env Vars

```
EURI_API_KEY=
HUBSPOT_API_KEY=
ZOHO_REFRESH_TOKEN=
ZOHO_CLIENT_ID=
ZOHO_CLIENT_SECRET=
AIRTABLE_API_KEY=
AIRTABLE_BASE_ID=
RESEND_API_KEY=
SLACK_WEBHOOK_URL=
```

---

## Rules of Engagement

- **Doubt = STOP.** Questions to Angelina if unsure:
  - "Which CRM is the primary target — HubSpot, Zoho, or Airtable MVP?"
  - "What's the scoring criteria for this business vertical?"
  - "Is lead routing round-robin or territory-based?"
  - "Follow-up track templates — do I generate or does Dhruv provide?"
- **Pluggable CRM backend** — build abstract `crm_client.py` with 3 implementations.
- **Idempotent** — every run respects `.tmp/last_run.json` so re-runs don't double-process.
- **Log errors immediately** to `../learning-hub/ERRORS.md`.

---

## Test Command

```bash
cd CRM_Automation
python tools/run_crm_cycle.py --source airtable --dry-run
# Expected: {"status":"success","leads_processed":N,"hot":X,"warm":Y,"cold":Z}
```

---

## When Done

1. Update `PROMPTS.md` + root `PROMPTS.md`
2. Add to `../learning-hub/automations/CATALOG.md`
3. Ping Angelina: "CRM Automation — built, tested, ready for deploy dispatch"
