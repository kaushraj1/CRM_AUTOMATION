"""Mock CRM backend — backed by .tmp/mock_crm.json.

Used for: dry-run testing, CI smoke tests, demos without real CRM credentials.
Initialized from tests/sample_leads.json on first use.
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from crm.base import CRMClient
from shared.logger import info, warn


PROJECT_ROOT = Path(__file__).parent.parent
MOCK_DB = PROJECT_ROOT / ".tmp" / "mock_crm.json"
SEED_FILE = PROJECT_ROOT / "tests" / "sample_leads.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MockCRMClient(CRMClient):
    source_name = "mock"

    def __init__(self):
        MOCK_DB.parent.mkdir(exist_ok=True)
        if not MOCK_DB.exists():
            self._seed()
        self._db = self._load()

    def _seed(self):
        if SEED_FILE.exists():
            shutil.copy(SEED_FILE, MOCK_DB)
            info(f"Mock CRM seeded from {SEED_FILE.name}")
        else:
            MOCK_DB.write_text(json.dumps({"leads": [], "tasks": []}, indent=2))
            warn("Mock CRM started empty — no seed file found at tests/sample_leads.json")

    def _load(self) -> dict:
        try:
            return json.loads(MOCK_DB.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            return {"leads": [], "tasks": []}

    def _save(self):
        MOCK_DB.write_text(json.dumps(self._db, indent=2))

    def fetch_leads(self, since: datetime, limit: int = 50) -> list[dict]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

        result = []
        for lead in self._db.get("leads", []):
            try:
                created = datetime.fromisoformat(lead["created_at"].replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
            except (ValueError, KeyError):
                continue
            if created >= since:
                normalized = self._normalize_base(lead)
                normalized.update({k: v for k, v in lead.items() if k != "raw"})
                normalized["source"] = "mock"
                result.append(normalized)
                if len(result) >= limit:
                    break

        info(f"Mock: fetched {len(result)} leads since {since.isoformat()}")
        return result

    def update_lead(self, lead_id: str, fields: dict) -> dict:
        for lead in self._db.get("leads", []):
            if lead.get("id") == lead_id:
                for k, v in fields.items():
                    if v is not None:
                        lead[k] = v
                lead["updated_at"] = _now_iso()
                self._save()
                info(f"Mock updated {lead_id}", fields=list(fields.keys()))
                return {"id": lead_id, "status": "updated", "message": f"updated {len(fields)} fields"}
        return {"id": lead_id, "status": "error", "message": "lead not found"}

    def create_task(self, lead_id: str, owner_email: str, title: str, due_at_iso: str) -> dict:
        task_id = f"mock_task_{len(self._db.get('tasks', [])) + 1}"
        task = {
            "task_id": task_id,
            "lead_id": lead_id,
            "owner_email": owner_email,
            "title": title,
            "due_at": due_at_iso,
            "created_at": _now_iso(),
            "status": "pending",
        }
        self._db.setdefault("tasks", []).append(task)
        self._save()
        return {"task_id": task_id, "status": "created"}

    def advance_stage(self, lead_id: str, normalized_stage: str) -> dict:
        result = self.update_lead(lead_id, {"stage": normalized_stage})
        result["stage_native"] = normalized_stage.capitalize()
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
