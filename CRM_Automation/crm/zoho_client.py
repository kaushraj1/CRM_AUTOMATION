"""Zoho CRM backend — Leads module.

Auth: OAuth2 server-based flow. Requires one-time manual setup to get a refresh token,
then every API call auto-refreshes the access_token (1-hour TTL).

Setup (one-time):
  1. Create Self-Client at https://api-console.zoho.com → Self Client
  2. Generate grant token with scope: ZohoCRM.modules.ALL,ZohoCRM.settings.ALL,ZohoCRM.users.READ
  3. Exchange grant for refresh token via:
       curl -X POST 'https://accounts.zoho.com/oauth/v2/token' \
         -d 'grant_type=authorization_code' \
         -d 'client_id=YOUR_CLIENT_ID' \
         -d 'client_secret=YOUR_CLIENT_SECRET' \
         -d 'code=YOUR_GRANT_TOKEN'
  4. Paste `refresh_token` into ZOHO_REFRESH_TOKEN in .env.

Region: US=zohoapis.com, EU=zohoapis.eu, IN=zohoapis.in, AU=zohoapis.com.au
Set ZOHO_ACCOUNT_URL accordingly.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from crm.base import CRMClient
from shared.logger import info, warn, error


ZOHO_TOKEN_CACHE = Path(__file__).parent.parent / ".tmp" / "zoho_token.json"


class ZohoClient(CRMClient):
    source_name = "zoho"

    STAGE_MAP = {
        "new": "Not Contacted",
        "contacted": "Contacted",
        "nurture": "Pre-Qualified",
        "qualified": "Qualified",
        "disqualified": "Lost Lead",
    }

    def __init__(self):
        self._refresh_token = os.getenv("ZOHO_REFRESH_TOKEN", "").strip()
        self._client_id = os.getenv("ZOHO_CLIENT_ID", "").strip()
        self._client_secret = os.getenv("ZOHO_CLIENT_SECRET", "").strip()
        self._api_base = os.getenv("ZOHO_ACCOUNT_URL", "https://www.zohoapis.com").strip()
        self._auth_base = self._api_base.replace("zohoapis", "accounts").replace("www.", "")
        if not all([self._refresh_token, self._client_id, self._client_secret]):
            raise EnvironmentError(
                "ZOHO_REFRESH_TOKEN, ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET are all required."
            )
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._load_cached_token()

    def _load_cached_token(self):
        if ZOHO_TOKEN_CACHE.exists():
            try:
                data = json.loads(ZOHO_TOKEN_CACHE.read_text())
                self._access_token = data.get("access_token")
                self._token_expires_at = data.get("expires_at", 0)
            except (json.JSONDecodeError, KeyError):
                pass

    def _save_cached_token(self):
        ZOHO_TOKEN_CACHE.parent.mkdir(exist_ok=True)
        ZOHO_TOKEN_CACHE.write_text(json.dumps({
            "access_token": self._access_token,
            "expires_at": self._token_expires_at,
        }))

    def _get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token

        try:
            r = requests.post(
                f"{self._auth_base}/oauth/v2/token",
                data={
                    "refresh_token": self._refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "refresh_token",
                },
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            if "access_token" not in data:
                raise ConnectionError(f"Zoho token refresh returned no access_token: {data}")
            self._access_token = data["access_token"]
            expires_in = int(data.get("expires_in", 3600))
            self._token_expires_at = now + expires_in
            self._save_cached_token()
            info(f"Zoho access token refreshed (expires in {expires_in}s)")
            return self._access_token
        except requests.RequestException as e:
            error(f"Zoho token refresh failed: {e}")
            raise ConnectionError(f"Zoho auth failed: {e}") from e

    def _headers(self) -> dict:
        return {
            "Authorization": f"Zoho-oauthtoken {self._get_access_token()}",
            "Content-Type": "application/json",
        }

    def _to_normalized(self, lead: dict) -> dict:
        norm = self._normalize_base(lead)
        norm["id"] = str(lead.get("id", ""))
        first = lead.get("First_Name") or ""
        last = lead.get("Last_Name") or ""
        norm["name"] = f"{first} {last}".strip()
        norm["email"] = (lead.get("Email") or "").lower()
        norm["company"] = lead.get("Company") or ""
        norm["title"] = lead.get("Designation") or ""
        norm["lead_source"] = (lead.get("Lead_Source") or "").lower()
        norm["created_at"] = lead.get("Created_Time") or ""
        norm["updated_at"] = lead.get("Modified_Time") or ""
        norm["score"] = lead.get("Lead_Score")
        band = lead.get("Lead_Band")
        norm["band"] = band.lower() if band else None
        owner = lead.get("Owner") or {}
        norm["owner_email"] = owner.get("email") if isinstance(owner, dict) else None
        norm["track"] = lead.get("Lead_Track")
        stage_raw = lead.get("Lead_Status") or "Not Contacted"
        norm["stage"] = {v: k for k, v in self.STAGE_MAP.items()}.get(stage_raw, "new")
        norm["do_not_contact"] = bool(lead.get("Email_Opt_Out", False))
        return norm

    def fetch_leads(self, since: datetime, limit: int = 50) -> list[dict]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        since_iso = since.strftime("%Y-%m-%dT%H:%M:%S+00:00")

        fields = "id,First_Name,Last_Name,Email,Company,Designation,Lead_Source,Created_Time,Modified_Time,Lead_Status,Email_Opt_Out,Owner,Lead_Score,Lead_Band,Lead_Track"
        criteria = f"(Created_Time:greater_than:{since_iso})"
        try:
            r = requests.get(
                f"{self._api_base}/crm/v5/Leads/search",
                headers=self._headers(),
                params={
                    "criteria": criteria,
                    "sort_by": "Created_Time",
                    "sort_order": "desc",
                    "per_page": min(limit, 200),
                    "fields": fields,
                },
                timeout=15,
            )
            if r.status_code in (204, 304):
                info("Zoho: no leads since last run")
                return []
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            error(f"Zoho fetch_leads failed: {e}")
            raise

        leads = data.get("data", [])
        normalized = [self._to_normalized(l) for l in leads]
        info(f"Zoho: fetched {len(normalized)} leads since {since_iso}")
        return normalized

    def update_lead(self, lead_id: str, fields: dict) -> dict:
        zoho_fields: dict = {}
        for key, value in fields.items():
            if value is None:
                continue
            if key == "score":
                zoho_fields["Lead_Score"] = value
            elif key == "band":
                zoho_fields["Lead_Band"] = value.lower() if isinstance(value, str) else str(value)
            elif key == "track":
                zoho_fields["Lead_Track"] = str(value)
            elif key == "stage":
                zoho_fields["Lead_Status"] = self.STAGE_MAP.get(value, value)
            elif key == "owner_email":
                zoho_fields["Owner"] = {"email": value}

        if not zoho_fields:
            return {"id": lead_id, "status": "skipped", "message": "no mapped fields"}

        try:
            r = requests.put(
                f"{self._api_base}/crm/v5/Leads/{lead_id}",
                headers=self._headers(),
                json={"data": [zoho_fields]},
                timeout=10,
            )
            r.raise_for_status()
            info(f"Zoho updated {lead_id}", fields=list(zoho_fields.keys()))
            return {"id": lead_id, "status": "updated", "message": f"updated {len(zoho_fields)} fields"}
        except requests.RequestException as e:
            error(f"Zoho update_lead failed for {lead_id}: {e}")
            return {"id": lead_id, "status": "error", "message": str(e)}

    def create_task(self, lead_id: str, owner_email: str, title: str, due_at_iso: str) -> dict:
        body = {
            "data": [{
                "Subject": title,
                "Status": "Not Started",
                "Priority": "High",
                "Due_Date": due_at_iso.split("T")[0],
                "What_Id": {"id": lead_id},
                "$se_module": "Leads",
                "Owner": {"email": owner_email},
            }]
        }
        try:
            r = requests.post(
                f"{self._api_base}/crm/v5/Tasks",
                headers=self._headers(),
                json=body,
                timeout=10,
            )
            r.raise_for_status()
            data = r.json().get("data", [])
            task_id = data[0]["details"]["id"] if data else ""
            return {"task_id": task_id, "status": "created"}
        except requests.RequestException as e:
            error(f"Zoho create_task failed: {e}")
            return {"task_id": "", "status": "error", "message": str(e)}

    def advance_stage(self, lead_id: str, normalized_stage: str) -> dict:
        native = self.STAGE_MAP.get(normalized_stage)
        if not native:
            return {"id": lead_id, "stage_native": "", "status": "skipped"}
        result = self.update_lead(lead_id, {"stage": normalized_stage})
        result["stage_native"] = native
        return result

    def get_health_stats(self, since: datetime) -> dict:
        leads = self.fetch_leads(since, limit=200)
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
