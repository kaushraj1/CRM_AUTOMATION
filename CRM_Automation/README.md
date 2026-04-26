# CRM Automation

> Hands-off lead lifecycle: auto-score → route → follow-up → advance stage → weekly report.
> Pluggable across **HubSpot · Zoho · Airtable · Mock** behind one interface.

---

## What It Does

Watches your CRM for new leads, scores each with an LLM (0-100), routes to the right owner with the right track (hot/warm/cold), fires the first touchpoint, creates a task in the owner's queue, advances the stage, and every week delivers a health report to Slack — all idempotent and budget-bounded.

```
Sense  →  Think         →  Decide                  →  Act                          →  Learn
new    →  LLM score    →  owner + track + stage   →  CRM update + email + task    →  weekly report
lead       (Euri)        (round-robin from yaml)    (CRM API + Resend)              (Slack)
```

---

## Architecture

```
┌─ workflows/lead-lifecycle.md       (the SOP — agent reads this)
├─ workflows/scoring-prompt-v1.md    (versioned LLM rubric)
│
├─ tools/                            (atomic CLIs, one job each)
│  ├─ fetch_leads.py
│  ├─ score_lead.py        ──▶ LLM via Euri (free) — heuristic fallback
│  ├─ route_lead.py        ──▶ round-robin from config/owners.yaml
│  ├─ update_crm.py        ──▶ writes score/owner/track/stage + tasks
│  ├─ send_followup.py     ──▶ Resend (free 100/day)
│  ├─ advance_stage.py
│  ├─ weekly_report.py     ──▶ markdown + Slack webhook
│  └─ run_crm_cycle.py     ──▶ orchestrator (idempotent)
│
├─ crm/                              (pluggable CRM backends)
│  ├─ base.py              (abstract CRMClient interface)
│  ├─ airtable_client.py
│  ├─ hubspot_client.py
│  ├─ zoho_client.py
│  ├─ mock_client.py       (offline — for testing/CI)
│  └─ factory.py           (get_client(source))
│
├─ config/
│  ├─ owners.yaml          (round-robin pool + capacity)
│  ├─ tracks.yaml          (3 tracks: first_call_24h, nurture_5_email, long_drip_monthly)
│  └─ stages.yaml          (per-CRM stage name mapping)
│
├─ shared/                 (env_loader, logger, cost_tracker, sandbox, retry, ...)
└─ tests/sample_leads.json (seed for mock backend)
```

---

## Quick Start (Offline Demo — No Creds Needed)

```bash
# 1. Install deps
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt

# 2. Run the test cycle with the mock CRM (uses tests/sample_leads.json)
.venv/bin/python tools/run_crm_cycle.py --source mock --dry-run --no-llm

# Expected output:
# {"status": "success", "source": "mock", "dry_run": true,
#  "leads_processed": 7, "hot": 2, "warm": 3, "cold": 2,
#  "log_path": "runs/YYYY-MM-DD-HHMMSS-lead-lifecycle.md"}
```

---

## Real Setup (Pick a CRM)

```bash
cp .env.example .env
# Fill the keys for your chosen backend (see below)
```

### Option A: Airtable (easiest MVP)

1. Create a base with a `Leads` table containing these columns:
   - `Name` (Single line text), `Email` (Email), `Company` (Single line text), `Title` (Single line text)
   - `Lead Source` (Single select: organic, demo_request, paid, referral, cold)
   - `Intent Signals` (Multiple select: pricing_page, demo_request, contact_sales, webinar, booked_meeting)
   - `Score` (Number), `Band` (Single select: Hot, Warm, Cold)
   - `Owner Email` (Email), `Track` (Single line text)
   - `Stage` (Single select: New, Contacted, Nurture, Qualified, Disqualified)
   - `Do Not Contact` (Checkbox), `Next Touch At` (Date), `Last Touch Msg` (Long text)
2. Get an API key from [airtable.com/create/tokens](https://airtable.com/create/tokens) with `data.records:read` + `data.records:write` scopes
3. Find your base ID at [airtable.com/api](https://airtable.com/api)
4. Set `AIRTABLE_API_KEY`, `AIRTABLE_BASE_ID`, `AIRTABLE_TABLE_NAME=Leads` in `.env`

### Option B: HubSpot (free CRM tier)

1. Create a Private App at *app.hubspot.com → Settings → Integrations → Private apps*
2. Scopes: `crm.objects.contacts.read`, `crm.objects.contacts.write`, `crm.objects.owners.read`, `crm.objects.tasks.read`, `crm.objects.tasks.write`
3. Add custom contact properties: `lead_score` (Number), `lead_band` (Single dropdown: hot/warm/cold), `lead_track` (Single line)
4. Set `HUBSPOT_API_KEY=pat-na1-xxx` in `.env`

### Option C: Zoho CRM (free 3 users)

1. Create a Self-Client at [api-console.zoho.com](https://api-console.zoho.com)
2. Generate a refresh token via OAuth flow (one-time, see `crm/zoho_client.py` docstring)
3. Add custom Lead fields: `Lead_Score` (Number), `Lead_Band` (Pick list: hot/warm/cold), `Lead_Track` (Single line)
4. Set `ZOHO_REFRESH_TOKEN`, `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_ACCOUNT_URL` in `.env`

### Common Keys (any backend)

```bash
EURI_API_KEY=...                 # Free at euron.one — LLM scoring
RESEND_API_KEY=re_...            # Free at resend.com — follow-up emails
EMAIL_FROM=onboarding@resend.dev # or your verified domain
SLACK_WEBHOOK_URL=https://...    # weekly health report channel
```

---

## Run

```bash
# Dry run — no writes, no emails
python tools/run_crm_cycle.py --source airtable --dry-run

# Live cycle — process up to 50 new leads
python tools/run_crm_cycle.py --source airtable --max-leads 50

# Heuristic only (no LLM) — useful when Euri quota is hit
python tools/run_crm_cycle.py --source airtable --no-llm

# Weekly report
python tools/weekly_report.py --source airtable --post-slack

# Individual tools (composable)
python tools/fetch_leads.py    --source airtable --max-leads 20
python tools/score_lead.py     --input .tmp/new_leads.json
python tools/route_lead.py     --input .tmp/scored_leads.json
python tools/update_crm.py     --source airtable --input .tmp/routed_leads.json --create-tasks
python tools/send_followup.py  --input .tmp/routed_leads.json --touch first
python tools/advance_stage.py  --source airtable --input .tmp/routed_leads.json
```

---

## Routing Rules

| Score | Band | Track | Owner | Task |
|-------|------|-------|-------|------|
| 90-100 | hot | `first_call_24h` | round-robin (sales pool) | "Call within 24h" |
| 60-89 | warm | `nurture_5_email` | round-robin (sales pool) | "Review at day 3" |
| 0-59 | cold | `long_drip_monthly` | nurture bot | none |

Customize the pool in `config/owners.yaml` and the email cadence in `config/tracks.yaml`.

---

## Idempotency

Re-running the same cycle within minutes is a **no-op**. Two safeguards:

1. State file `.tmp/last_run.json` advances `last_run_at` only on successful completion
2. `processed_lead_ids[source]` is checked even within the time window

Crashed mid-run? State doesn't advance — re-run picks up from the last good state.

---

## Deployment (Later — DO NOT during build)

| Cadence | Option |
|---------|--------|
| Every 15 min | n8n schedule node → HTTP wrapper → `run_crm_cycle.py` |
| Hourly batch | GitHub Actions cron — see root `DEPLOY.md` |
| Manual | local cron / Airflow / Prefect |

---

## Cost Per Cycle (≤ 50 leads)

| Component | Cost |
|-----------|------|
| CRM API calls | $0 (free tiers) |
| Euri LLM scoring | ~$0.001 × leads (free 200K tokens/day) |
| Resend email | $0 (100/day free) |
| Slack | $0 |
| **Total** | **≤ $0.05** |

Daily budget cap enforced at $5 via `shared/cost_tracker.py` — `BudgetExceededError` is **never** silently caught.

---

**Phase:** 3 — No-Code Automation (Week 7: Database, CRM & Business Logic)
**Owner:** Atlas
**Status:** Built, tested with mock backend, awaiting deploy dispatch
