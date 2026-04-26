"""HubSpot CRM backend — Contacts object (free tier friendly).

Auth: Private App Token (HUBSPOT_API_KEY) with scopes:
  crm.objects.contacts.read, crm.objects.contacts.write,
  crm.schemas.contacts.read, crm.objects.owners.read,
  tickets (optional for tasks → create engagements),
  tasks (crm.objects.tasks.read/write)

Create the private app at:
  app.hubspot.com → Settings → Integrations → Private apps
"""

import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from crm.base import CRMClient
from shared.logger import info, warn, error


HUBSPOT_BASE = "https://api.hubapi.com"


class HubSpotClient(CRMClient):
    source_name = "hubspot"

    # HubSpot property names → normalized keys
    # These are the most common default properties; add custom ones via .env if needed.
    FIELD_MAP = {
        "name": "firstname",   # combined with lastname in normalize
        "email": "email",
        "company": "company",
        "title": "jobtitle",
        "lead_source": "hs_analytics_source",
        "score": "hubspotscore",  # HubSpot has its own score; we overwrite with custom "lead_score"
        "custom_score": "lead_score",  # custom property — user must create
        "band": "lead_band",            # custom property — user must create
        "owner_email": "hubspot_owner_id",  # note: owner_id, not email — resolved via /owners/
        "track": "lead_track",          # custom property
        "stage": "lifecyclestage",
        "do_not_contact": "hs_email_optout",
    }

    STAGE_MAP = {
        "new": "lead",
        "contacted": "salesqualifiedlead",
        "nurture": "subscriber",
        "qualified": "opportunity",
        "disqualified": "other",
    }

    def __init__(self):
        token = os.getenv("HUBSPOT_API_KEY", "").strip()
        if not token:
            raise EnvironmentError("HUBSPOT_API_KEY is required. Add it to .env.")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._owner_cache: dict[str, str] = {}  # email → owner_id

    def _resolve_owner_id(self, owner_email: str) -> str | None:
        if owner_email in self._owner_cache:
            return self._owner_cache[owner_email]
        try:
            r = requests.get(
                f"{HUBSPOT_BASE}/crm/v3/owners/",
                headers=self._headers,
                params={"email": owner_email, "limit": 1},
                timeout=10,
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                warn(f"HubSpot: no owner found for {owner_email}")
                return None
            owner_id = str(results[0]["id"])
            self._owner_cache[owner_email] = owner_id
            return owner_id
        except requests.RequestException as e:
            error(f"HubSpot owner lookup failed: {e}")
            return None

    def _to_normalized(self, contact: dict) -> dict:
        props = contact.get("properties", {})
        norm = self._normalize_base(contact)
        norm["id"] = str(contact.get("id", ""))
        first = props.get("firstname") or ""
        last = props.get("lastname") or ""
        norm["name"] = f"{first} {last}".strip()
        norm["email"] = (props.get("email") or "").lower()
        norm["company"] = props.get("company") or ""
        norm["title"] = props.get("jobtitle") or ""
        norm["lead_source"] = (props.get("hs_analytics_source") or "").lower()
        norm["created_at"] = props.get("createdate") or ""
        norm["updated_at"] = props.get("lastmodifieddate") or ""
        norm["score"] = int(props["lead_score"]) if props.get("lead_score") else None
        band = props.get("lead_band")
        norm["band"] = band.lower() if band else None
        norm["owner_email"] = None  # resolved via owners endpoint if needed
        norm["track"] = props.get("lead_track")
        stage_raw = props.get("lifecyclestage") or "lead"
        norm["stage"] = {v: k for k, v in self.STAGE_MAP.items()}.get(stage_raw, "new")
        norm["do_not_contact"] = bool(props.get("hs_email_optout"))
        return norm

    def fetch_leads(self, since: datetime, limit: int = 50) -> list[dict]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        since_ms = int(since.timestamp() * 1000)

        body = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "createdate",
                    "operator": "GTE",
                    "value": str(since_ms),
                }]
            }],
            "properties": [
                "firstname", "lastname", "email", "company", "jobtitle",
                "hs_analytics_source", "createdate", "lastmodifieddate",
                "lead_score", "lead_band", "lead_track", "lifecyclestage",
                "hs_email_optout",
            ],
            "limit": min(limit, 100),
            "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}],
        }

        try:
            r = requests.post(
                f"{HUBSPOT_BASE}/crm/v3/objects/contacts/search",
                headers=self._headers,
                json=body,
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            error(f"HubSpot fetch_leads failed: {e}")
            raise

        contacts = data.get("results", [])
        normalized = [self._to_normalized(c) for c in contacts]
        info(f"HubSpot: fetched {len(normalized)} contacts since {since.isoformat()}")
        return normalized

    def update_lead(self, lead_id: str, fields: dict) -> dict:
        props: dict = {}
        for key, value in fields.items():
            if value is None:
                continue
            if key == "score":
                props["lead_score"] = str(value)
            elif key == "band":
                props["lead_band"] = value.lower() if isinstance(value, str) else str(value)
            elif key == "track":
                props["lead_track"] = str(value)
            elif key == "stage":
                props["lifecyclestage"] = self.STAGE_MAP.get(value, value)
            elif key == "owner_email":
                owner_id = self._resolve_owner_id(value)
                if owner_id:
                    props["hubspot_owner_id"] = owner_id
            elif key in self.FIELD_MAP:
                props[self.FIELD_MAP[key]] = value

        if not props:
            return {"id": lead_id, "status": "skipped", "message": "no mapped fields"}

        try:
            r = requests.patch(
                f"{HUBSPOT_BASE}/crm/v3/objects/contacts/{lead_id}",
                headers=self._headers,
                json={"properties": props},
                timeout=10,
            )
            r.raise_for_status()
            info(f"HubSpot updated {lead_id}", fields=list(props.keys()))
            return {"id": lead_id, "status": "updated", "message": f"updated {len(props)} props"}
        except requests.RequestException as e:
            error(f"HubSpot update_lead failed for {lead_id}: {e}")
            return {"id": lead_id, "status": "error", "message": str(e)}

    def create_task(self, lead_id: str, owner_email: str, title: str, due_at_iso: str) -> dict:
        owner_id = self._resolve_owner_id(owner_email)
        if not owner_id:
            return {"task_id": "", "status": "skipped", "message": "owner not found"}

        # Parse ISO to ms timestamp for HubSpot
        try:
            due_dt = datetime.fromisoformat(due_at_iso.replace("Z", "+00:00"))
            due_ms = int(due_dt.timestamp() * 1000)
        except ValueError:
            due_ms = int((datetime.now(timezone.utc).timestamp() + 86400) * 1000)

        body = {
            "properties": {
                "hs_task_subject": title,
                "hs_task_body": title,
                "hs_task_status": "NOT_STARTED",
                "hs_task_priority": "HIGH",
                "hs_task_type": "CALL",
                "hs_timestamp": str(due_ms),
                "hubspot_owner_id": owner_id,
            },
            "associations": [{
                "to": {"id": lead_id},
                "types": [{
                    "associationCategory": "HUBSPOT_DEFINED",
                    "associationTypeId": 204,  # task-to-contact
                }],
            }],
        }
        try:
            r = requests.post(
                f"{HUBSPOT_BASE}/crm/v3/objects/tasks",
                headers=self._headers,
                json=body,
                timeout=10,
            )
            r.raise_for_status()
            task_id = r.json().get("id", "")
            return {"task_id": task_id, "status": "created"}
        except requests.RequestException as e:
            error(f"HubSpot create_task failed: {e}")
            return {"task_id": "", "status": "error", "message": str(e)}

    def advance_stage(self, lead_id: str, normalized_stage: str) -> dict:
        native = self.STAGE_MAP.get(normalized_stage)
        if not native:
            return {"id": lead_id, "stage_native": "", "status": "skipped"}
        result = self.update_lead(lead_id, {"stage": normalized_stage})
        result["stage_native"] = native
        return result

    def get_health_stats(self, since: datetime) -> dict:
        leads = self.fetch_leads(since, limit=500)
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
