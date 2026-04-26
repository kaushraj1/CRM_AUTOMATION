"""Track API costs per run and enforce budget limits."""

import json
from datetime import datetime, timezone
from pathlib import Path
from shared.logger import warn

COST_FILE = Path(__file__).parent.parent / ".tmp" / "costs.json"
DAILY_LIMIT = 5.00
RUN_LIMIT = 2.00


class BudgetExceededError(Exception):
    pass


def _load_costs() -> dict:
    if COST_FILE.exists():
        return json.loads(COST_FILE.read_text())
    return {"runs": []}


def _save_costs(data: dict):
    COST_FILE.parent.mkdir(exist_ok=True)
    COST_FILE.write_text(json.dumps(data, indent=2))


def get_daily_spend() -> float:
    data = _load_costs()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return sum(r["cost"] for r in data["runs"] if r["date"] == today)


def check_budget(estimated_cost: float = 0.0):
    """Check if we're within budget. Raises BudgetExceededError if not."""
    daily = get_daily_spend()
    if daily + estimated_cost > DAILY_LIMIT:
        raise BudgetExceededError(
            f"Daily budget exceeded: ${daily:.2f} spent, ${estimated_cost:.2f} requested, limit ${DAILY_LIMIT:.2f}"
        )
    if estimated_cost > RUN_LIMIT:
        warn(f"Single run cost ${estimated_cost:.2f} exceeds per-run limit ${RUN_LIMIT:.2f}")


def record_cost(tool: str, cost: float):
    """Record a cost entry for today."""
    data = _load_costs()
    data["runs"].append({
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "time": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "cost": cost,
    })
    _save_costs(data)
