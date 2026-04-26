"""Score leads 0-100 — LLM via Euri (preferred) or deterministic heuristic fallback.

Usage:
    python tools/score_lead.py --input .tmp/new_leads.json --output .tmp/scored_leads.json
    python tools/score_lead.py --input .tmp/new_leads.json --no-llm   # heuristic only

Output:
    Writes scored leads to --output. Each lead gets `score`, `band`, `reasoning`, `signals`.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.env_loader import load_env, get_optional
from shared.logger import info, error, warn
from shared.cost_tracker import check_budget, record_cost
from shared.sandbox import validate_write_path


PROJECT_ROOT = Path(__file__).parent.parent
SCORING_PROMPT_PATH = PROJECT_ROOT / "workflows" / "scoring-prompt-v1.md"


PERSONAL_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "aol.com"}


def _band_from_score(score: int) -> str:
    if score >= 90:
        return "hot"
    if score >= 60:
        return "warm"
    return "cold"


def heuristic_score(lead: dict) -> dict:
    """Deterministic rule-based scoring — mirrors the LLM rubric in workflows/scoring-prompt-v1.md."""
    title = (lead.get("title") or "").lower()
    signals = [(s or "").lower() for s in lead.get("intent_signals") or []]
    source = (lead.get("lead_source") or "").lower()
    email = (lead.get("email") or "").lower()
    name = lead.get("name") or ""
    company = lead.get("company") or ""
    raw = lead.get("raw") or {}

    score = 0
    matched: list[str] = []

    # Profile fit (40 max) = title (25 max) + company-size proxy (15 max)
    if any(t in title for t in ["vp", "chief", "cxo", "founder", "ceo", "cto", "coo", "cmo", "cfo"]):
        score += 25; matched.append("title:csuite")
    elif "head of" in title or "director" in title:
        score += 15; matched.append("title:senior")
    elif any(t in title for t in ["manager", "lead", "principal"]):
        score += 8; matched.append("title:manager")
    elif title:
        score += 3; matched.append("title:ic")

    company_lower = company.lower()
    big_co_signals = ["corp", "global", "industries", "enterprise", "group", "international", "holdings"]
    if any(k in company_lower for k in big_co_signals):
        score += 15; matched.append("company:large")
    elif company:
        score += 8; matched.append("company:has")

    # Intent (35)
    intent_weights = {"booked_meeting": 25, "demo": 20, "contact_sales": 15, "pricing": 10, "webinar": 5}
    intent_score = 0
    for keyword, weight in intent_weights.items():
        if any(keyword in s for s in signals):
            intent_score += weight
            matched.append(f"intent:{keyword}")
    score += min(35, intent_score)

    # Source (15)
    source_weights = {"organic": 15, "referral": 15, "demo_request": 14, "content": 12, "partner": 10, "paid": 5, "cold": 2}
    src_score = 5  # default for unknown
    for key, weight in source_weights.items():
        if key in source:
            src_score = weight
            matched.append(f"source:{key}")
            break
    score += src_score

    # Recency (10)
    try:
        created = datetime.fromisoformat((lead.get("created_at") or "").replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - created).days
        if age_days < 1:
            score += 10; matched.append("recency:fresh")
        elif age_days < 7:
            score += 7; matched.append("recency:week")
        elif age_days < 30:
            score += 3; matched.append("recency:month")
    except (ValueError, AttributeError):
        pass

    # Hard overrides
    domain = email.split("@")[-1] if "@" in email else ""
    if domain in PERSONAL_DOMAINS:
        score = min(score, 55); matched.append("override:personal_email")
    if not name and not company:
        score = min(score, 30); matched.append("override:no_identity")
    if raw.get("do_not_contact") or raw.get("unsubscribed") or lead.get("do_not_contact"):
        score = 0; matched.append("override:do_not_contact")

    score = max(0, min(100, score))
    return {
        "score": score,
        "band": _band_from_score(score),
        "signals": matched,
        "reasoning": f"Heuristic: {' + '.join(matched) if matched else 'no signals'}",
        "method": "heuristic",
    }


def llm_score(lead: dict, prompt_template: str) -> dict | None:
    """Score via Euri (or OpenRouter fallback). Returns None on failure."""
    try:
        from openai import OpenAI
    except ImportError:
        warn("openai package not installed — using heuristic fallback")
        return None

    euri_key = get_optional("EURI_API_KEY")
    openrouter_key = get_optional("OPENROUTER_API_KEY")

    if euri_key:
        client = OpenAI(base_url="https://api.euron.one/api/v1/euri", api_key=euri_key)
        model = "gpt-4o-mini"
        provider = "euri"
    elif openrouter_key:
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=openrouter_key)
        model = "openai/gpt-4o-mini"
        provider = "openrouter"
    else:
        return None

    user_prompt = prompt_template.format(
        name=lead.get("name") or "(missing)",
        email=lead.get("email") or "(missing)",
        company=lead.get("company") or "(missing)",
        title=lead.get("title") or "(missing)",
        source=lead.get("lead_source") or "(unknown)",
        intent_signals=", ".join(lead.get("intent_signals") or []) or "(none)",
        created_at=lead.get("created_at") or "(unknown)",
        raw_notes=json.dumps(lead.get("raw") or {}, default=str)[:500],
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a B2B SaaS lead-scoring analyst. Return ONLY strict JSON."},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=300,
        )
        content = (response.choices[0].message.content or "").strip()
        json_match = re.search(r'\{[\s\S]*\}', content)
        if not json_match:
            warn(f"LLM returned no JSON for lead {lead.get('id')}")
            return None

        parsed = json.loads(json_match.group())
        score = int(parsed.get("score", 0))
        score = max(0, min(100, score))
        record_cost("score_lead", 0.001)

        return {
            "score": score,
            "band": (parsed.get("band") or _band_from_score(score)).lower(),
            "signals": parsed.get("signals") or [],
            "reasoning": parsed.get("reasoning") or "",
            "method": f"llm:{provider}",
        }
    except Exception as e:
        warn(f"LLM scoring failed for lead {lead.get('id')}: {type(e).__name__}: {e}")
        return None


def _build_user_template(prompt_md: str) -> str:
    """Extract just the User Prompt Template section (between fenced 'User Prompt Template' and next heading)."""
    # Simpler approach: synthesize from rubric — keep template coherent regardless of prompt-file edits
    return (
        "Score the lead below. Return ONLY JSON: "
        "{{\"score\": int 0-100, \"band\": \"hot|warm|cold\", "
        "\"signals\": [\"...\"], \"reasoning\": \"one sentence\"}}\n\n"
        "Lead:\n"
        "- Name: {name}\n"
        "- Email: {email}\n"
        "- Company: {company}\n"
        "- Title: {title}\n"
        "- Source: {source}\n"
        "- Intent signals: {intent_signals}\n"
        "- Created at: {created_at}\n"
        "- Raw notes: {raw_notes}\n"
    )


def main():
    parser = argparse.ArgumentParser(description="Score leads 0-100")
    parser.add_argument("--input", default=".tmp/new_leads.json")
    parser.add_argument("--output", default=".tmp/scored_leads.json")
    parser.add_argument("--no-llm", action="store_true", help="Use heuristic only (skip LLM)")
    args = parser.parse_args()

    load_env()

    input_path = PROJECT_ROOT / args.input
    if not input_path.exists():
        error(f"Input file not found: {input_path}")
        sys.exit(1)

    leads = json.loads(input_path.read_text())
    if not leads:
        info("No leads to score — writing empty output")
        validate_write_path(str(PROJECT_ROOT / args.output)).write_text("[]")
        print(json.dumps({"status": "success", "scored": 0, "method": "skip"}))
        return

    estimated = 0.001 * len(leads)
    check_budget(estimated_cost=estimated)

    prompt_md = SCORING_PROMPT_PATH.read_text() if SCORING_PROMPT_PATH.exists() else ""
    user_template = _build_user_template(prompt_md)

    method_counts = {"llm": 0, "heuristic": 0}
    band_counts = {"hot": 0, "warm": 0, "cold": 0}

    for lead in leads:
        scoring = None
        if not args.no_llm:
            scoring = llm_score(lead, user_template)

        if scoring is None:
            scoring = heuristic_score(lead)

        lead.update(scoring)
        if scoring["method"].startswith("llm"):
            method_counts["llm"] += 1
        else:
            method_counts["heuristic"] += 1
        band_counts[scoring["band"]] = band_counts.get(scoring["band"], 0) + 1

    output_path = validate_write_path(str(PROJECT_ROOT / args.output))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(leads, indent=2, ensure_ascii=False))

    print(json.dumps({
        "status": "success",
        "scored": len(leads),
        "by_method": method_counts,
        "by_band": band_counts,
        "output_path": str(output_path),
    }))


if __name__ == "__main__":
    main()
