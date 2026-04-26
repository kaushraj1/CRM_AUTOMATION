"""Airtable CRM backend — MVP / demo target.

Expected table schema (columns in your Airtable base):
  Name              — Single line text
  Email             — Email
  Company           — Single line text
  Title             — Single line text
  Lead Source       — Single select  (organic | demo_request | paid | referral | cold)
  Intent Signals    — Multiple select (pricing_page | demo_request | contact_sales | webinar | booked_meeting)
  Created At        — Created time   (auto)
  Score             — Number
  Band              — Single select  (hot | warm | cold)
  Owner Email       — Email
  Track             — Single line text
  Stage             — Single select  (New | Contacted | Nurture | Qualified | Disqualified)
  Do Not Contact    — Checkbox
  Next Touch At     — Date
  Last Touch Msg    — Long text
"""

import os
from datetime import datetime, timezone
from typing import Any

from crm.base import CRMClient
from shared.logger import info, warn, error


class AirtableClient(CRMClient):
    source_name = "airtable"

    FIELD_MAP = {
        "name": "Name",
        "email": "Email",
        "company": "Company",
        "title": "Title",
        "lead_source": "Lead Source",
        "intent_signals": "Intent Signals",
        "score": "Score",
        "band": "Band",
        "owner_email": "Owner Email",
        "track": "Track",
        "stage": "Stage",
        "do_not_contact": "Do Not Contact",
        "next_touch_at": "Next Touch At",
        "last_touch_msg": "Last Touch Msg",
    }

    def __init__(self):
        api_key = os.getenv("AIRTABLE_API_KEY", "").strip()
        base_id = os.getenv("AIRTABLE_BASE_ID", "").strip()
        table_name = os.getenv("AIRTABLE_TABLE_NAME", "Leads").strip()
        if not api_key or not base_id:
            raise EnvironmentError(
                "AIRTABLE_API_KEY and AIRTABLE_BASE_ID are required. Add them to .env."
            )

        try:
            from pyairtable import Api
        except ImportError as e:
            raise ImportError("pyairtable not installed. Run: pip install pyairtable") from e

        self._api = Api(api_key)
        self._table = self._api.table(base_id, table_name)
        self._table_name = table_name

    def _to_normalized(self, record: dict) -> dict:
        fields = record.get("fields", {})
        norm = self._normalize_base(record)
        norm["id"] = record.get("id", "")
        norm["created_at"] = record.get("createdTime", "")
        norm["updated_at"] = record.get("createdTime", "")  # Airtable lacks updated_at in base
        norm["name"] = fields.get(self.FIELD_MAP["name"], "") or ""
        norm["email"] = (fields.get(self.FIELD_MAP["email"], "") or "").lower()
        norm["company"] = fields.get(self.FIELD_MAP["company"], "") or ""
        norm["title"] = fields.get(self.FIELD_MAP["title"], "") or ""
        norm["lead_source"] = (fields.get(self.FIELD_MAP["lead_source"], "") or "").lower()
        norm["intent_signals"] = fields.get(self.FIELD_MAP["intent_signals"], []) or []
        norm["score"] = fields.get(self.FIELD_MAP["score"])
        band = fields.get(self.FIELD_MAP["band"])
        norm["band"] = band.lower() if band else None
        norm["owner_email"] = fields.get(self.FIELD_MAP["owner_email"])
        norm["track"] = fields.get(self.FIELD_MAP["track"])
        stage_raw = fields.get(self.FIELD_MAP["stage"], "New")
        norm["stage"] = self._denormalize_stage(stage_raw)
        norm["do_not_contact"] = bool(fields.get(self.FIELD_MAP["do_not_contact"], False))
        return norm

    @staticmethod
    def _denormalize_stage(stage_raw: str) -> str:
        return {
            "New": "new",
            "Contacted": "contacted",
            "Nurture": "nurture",
            "Qualified": "qualified",
            "Disqualified": "disqualified",
        }.get(stage_raw, "new")

    def fetch_leads(self, since: datetime, limit: int = 50) -> list[dict]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        since_iso = since.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        # Airtable CREATED_TIME() comparison — filterByFormula
        formula = f"IS_AFTER(CREATED_TIME(), '{since_iso}')"

        try:
            records = self._table.all(formula=formula, max_records=limit)
        except Exception as e:
            error(f"Airtable fetch_leads failed: {e}")
            raise

        normalized = [self._to_normalized(r) for r in records]
        info(f"Airtable: fetched {len(normalized)} leads since {since_iso}")
        return normalized

    def update_lead(self, lead_id: str, fields: dict) -> dict:
        airtable_fields: dict[str, Any] = {}
        for key, value in fields.items():
            if key in self.FIELD_MAP and value is not None:
                at_key = self.FIELD_MAP[key]
                if key == "band" and isinstance(value, str):
                    airtable_fields[at_key] = value.capitalize()
                elif key == "stage" and isinstance(value, str):
                    airtable_fields[at_key] = value.capitalize()
                else:
                    airtable_fields[at_key] = value

        if not airtable_fields:
            return {"id": lead_id, "status": "skipped", "message": "no mapped fields"}

        try:
            self._table.update(lead_id, airtable_fields)
            info(f"Airtable updated {lead_id}", fields=list(airtable_fields.keys()))
            return {"id": lead_id, "status": "updated", "message": f"updated {len(airtable_fields)} fields"}
        except Exception as e:
            error(f"Airtable update_lead failed for {lead_id}: {e}")
            return {"id": lead_id, "status": "error", "message": str(e)}

    def create_task(self, lead_id: str, owner_email: str, title: str, due_at_iso: str) -> dict:
        # Airtable has no native task model — we write to Last Touch Msg + Next Touch At
        # as a lightweight surrogate. Real deployments add a separate Tasks table.
        try:
            self._table.update(
                lead_id,
                {
                    self.FIELD_MAP["next_touch_at"]: due_at_iso,
                    self.FIELD_MAP["last_touch_msg"]: f"TASK for {owner_email}: {title}",
                },
            )
            return {"task_id": f"airtable:{lead_id}:task", "status": "created"}
        except Exception as e:
            error(f"Airtable create_task failed: {e}")
            return {"task_id": "", "status": "error", "message": str(e)}

    def advance_stage(self, lead_id: str, normalized_stage: str) -> dict:
        stage_map = {
            "new": "New",
            "contacted": "Contacted",
            "nurture": "Nurture",
            "qualified": "Qualified",
            "disqualified": "Disqualified",
        }
        native = stage_map.get(normalized_stage)
        if not native:
            return {"id": lead_id, "stage_native": "", "status": "skipped"}
        try:
            self._table.update(lead_id, {self.FIELD_MAP["stage"]: native})
            return {"id": lead_id, "stage_native": native, "status": "updated"}
        except Exception as e:
            error(f"Airtable advance_stage failed: {e}")
            return {"id": lead_id, "stage_native": native, "status": "error", "message": str(e)}

    def get_health_stats(self, since: datetime) -> dict:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        since_iso = since.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        formula = f"IS_AFTER(CREATED_TIME(), '{since_iso}')"

        try:
            records = self._table.all(formula=formula)
        except Exception as e:
            error(f"Airtable health stats failed: {e}")
            return {"new_leads": 0, "scored": 0, "hot": 0, "warm": 0, "cold": 0,
                    "contacted": 0, "qualified": 0, "conversion_rate_pct": 0.0, "top_sources": []}

        leads = [self._to_normalized(r) for r in records]
        scored = [l for l in leads if l.get("score") is not None]
        hot = sum(1 for l in scored if (l.get("band") or "") == "hot")
        warm = sum(1 for l in scored if (l.get("band") or "") == "warm")
        cold = sum(1 for l in scored if (l.get("band") or "") == "cold")
        contacted = sum(1 for l in leads if l.get("stage") in ("contacted", "qualified"))
        qualified = sum(1 for l in leads if l.get("stage") == "qualified")
        total = len(leads) or 1
        conversion = round(100 * qualified / total, 1)

        sources: dict[str, int] = {}
        for l in leads:
            s = l.get("lead_source", "") or "unknown"
            sources[s] = sources.get(s, 0) + 1
        top_sources = sorted(
            [{"source": k, "count": v} for k, v in sources.items()],
            key=lambda x: x["count"], reverse=True,
        )[:5]

        return {
            "new_leads": len(leads),
            "scored": len(scored),
            "hot": hot, "warm": warm, "cold": cold,
            "contacted": contacted, "qualified": qualified,
            "conversion_rate_pct": conversion,
            "top_sources": top_sources,
        }
