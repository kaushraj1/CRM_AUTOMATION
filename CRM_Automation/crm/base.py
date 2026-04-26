"""Abstract CRMClient — the single interface every CRM backend implements.

All tools talk to this interface. Tools never import a concrete backend.
Swap CRMs by changing `--source` — no tool code changes.
"""

from abc import ABC, abstractmethod
from datetime import datetime


# Normalized lead shape — every backend returns leads in this shape.
# Tools expect these keys. Missing optional fields are None/"".
#
# {
#   "id": str,                # CRM-native record ID
#   "source": str,            # "airtable" | "hubspot" | "zoho"
#   "name": str,
#   "email": str,
#   "company": str,
#   "title": str,
#   "lead_source": str,       # "organic" | "demo_request" | "paid" | ...
#   "intent_signals": [str],  # ["pricing_page", "demo_request", ...]
#   "created_at": str,        # ISO 8601 UTC
#   "updated_at": str,
#   "score": int | None,
#   "band": str | None,       # "hot" | "warm" | "cold"
#   "owner_email": str | None,
#   "track": str | None,
#   "stage": str,             # normalized key from config/stages.yaml
#   "do_not_contact": bool,
#   "raw": dict,              # original CRM record — for debugging
# }


class CRMClient(ABC):
    """Interface for CRM backends. Every method is mandatory."""

    source_name: str = "base"  # override in concrete class

    @abstractmethod
    def fetch_leads(self, since: datetime, limit: int = 50) -> list[dict]:
        """Return new/changed leads since `since` as normalized dicts.

        Args:
            since: UTC datetime — only leads created_at or updated_at >= since
            limit: max records to return

        Returns:
            List of normalized lead dicts (see shape above)
        """

    @abstractmethod
    def update_lead(self, lead_id: str, fields: dict) -> dict:
        """Update a single lead with the given fields.

        Args:
            lead_id: CRM-native record ID
            fields: normalized field dict — implementation maps to CRM field names

        Returns:
            {"id": str, "status": "updated" | "skipped", "message": str}
        """

    @abstractmethod
    def create_task(self, lead_id: str, owner_email: str, title: str, due_at_iso: str) -> dict:
        """Create a task for `owner_email` on `lead_id`.

        Returns:
            {"task_id": str, "status": "created" | "skipped"}
        """

    @abstractmethod
    def advance_stage(self, lead_id: str, normalized_stage: str) -> dict:
        """Move lead to the given normalized stage.

        Args:
            normalized_stage: one of: new, contacted, nurture, qualified, disqualified

        Returns:
            {"id": str, "stage_native": str, "status": "updated" | "skipped"}
        """

    @abstractmethod
    def get_health_stats(self, since: datetime) -> dict:
        """Return aggregate counts for weekly report.

        Returns:
            {
              "new_leads": int,
              "scored": int,
              "hot": int, "warm": int, "cold": int,
              "contacted": int, "qualified": int,
              "conversion_rate_pct": float,
              "top_sources": [{"source": str, "count": int}, ...],
            }
        """

    # ─── Shared helpers (concrete — inherited) ──────────────────────────────

    def _normalize_base(self, raw: dict) -> dict:
        """Seed a normalized dict with defaults. Subclasses fill in."""
        return {
            "id": "",
            "source": self.source_name,
            "name": "",
            "email": "",
            "company": "",
            "title": "",
            "lead_source": "",
            "intent_signals": [],
            "created_at": "",
            "updated_at": "",
            "score": None,
            "band": None,
            "owner_email": None,
            "track": None,
            "stage": "new",
            "do_not_contact": False,
            "raw": raw,
        }
