"""
Simulated Salesforce CRM service.
In production, replace with the Salesforce REST/Bulk API.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_RM_ASSIGNMENTS: dict[str, dict] = {
    "APP-2026-0301": {"name": "Sarah Chen", "email": "schen@crestview.com", "team": "Institutional"},
    "APP-2026-0302": {"name": "David Park", "email": "dpark@crestview.com", "team": "High Net Worth"},
    "APP-2026-0303": {"name": "TBD", "email": "compliance@crestview.com", "team": "Compliance Review"},
    "APP-2026-0304": {"name": "James Whitmore", "email": "jwhitmore@crestview.com", "team": "Institutional"},
    "APP-2026-0305": {"name": "Emily Rodriguez", "email": "erodriguez@crestview.com", "team": "High Net Worth"},
    "APP-2026-0306": {"name": "Sarah Chen", "email": "schen@crestview.com", "team": "Institutional"},
    "APP-2026-0307": {"name": "TBD", "email": "compliance@crestview.com", "team": "Compliance Review"},
    "APP-2026-0308": {"name": "David Park", "email": "dpark@crestview.com", "team": "High Net Worth"},
    "APP-2026-0309": {"name": "James Whitmore", "email": "jwhitmore@crestview.com", "team": "Institutional"},
    "APP-2026-0310": {"name": "Emily Rodriguez", "email": "erodriguez@crestview.com", "team": "High Net Worth"},
    "APP-2026-0311": {"name": "TBD", "email": "compliance@crestview.com", "team": "Compliance Review"},
    "APP-2026-0312": {"name": "Sarah Chen", "email": "schen@crestview.com", "team": "Institutional"},
    "APP-2026-0313": {"name": "Emily Rodriguez", "email": "erodriguez@crestview.com", "team": "High Net Worth"},
    "APP-2026-0314": {"name": "James Whitmore", "email": "jwhitmore@crestview.com", "team": "Institutional"},
    "APP-2026-0315": {"name": "TBD", "email": "compliance@crestview.com", "team": "Compliance Review"},
}


class SalesforceService:
    def __init__(self) -> None:
        self._opportunities: dict[str, dict] = {}

    def update_opportunity_status(self, client_id: str, status: str) -> dict:
        self._opportunities[client_id] = {
            "client_id": client_id,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info("[Salesforce] UPDATE_OPPORTUNITY | client=%s | status=%s", client_id, status)
        return {"success": True, "client_id": client_id, "new_status": status}

    def get_relationship_manager(self, client_id: str) -> dict:
        rm = _RM_ASSIGNMENTS.get(client_id, {"name": "Unassigned", "email": "ops@crestview.com", "team": "Operations"})
        logger.info(
            "[Salesforce] GET_RM | client=%s | rm=%s | team=%s",
            client_id,
            rm["name"],
            rm["team"],
        )
        return rm
