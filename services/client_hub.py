"""
Simulated ClientHub service — represents the firm's internal client management system.
In production, swap this class for one that calls the real ClientHub REST API.
"""
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ClientHubService:
    def __init__(self) -> None:
        self._store: dict[str, dict] = {}

    def write_client_record(self, client_data: dict) -> dict:
        client_id = client_data.get("application_id", str(uuid.uuid4()))
        record = {
            **client_data,
            "hub_record_id": f"HUB-{client_id}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "system": "ClientHub v4.2",
        }
        self._store[client_id] = record
        logger.info(
            "[ClientHub] WRITE | client_id=%s | record_id=%s | name=%s",
            client_id,
            record["hub_record_id"],
            client_data.get("client_name", "unknown"),
        )
        return {"success": True, "hub_record_id": record["hub_record_id"]}

    def read_client_record(self, client_id: str) -> dict | None:
        record = self._store.get(client_id)
        if record:
            logger.info("[ClientHub] READ | client_id=%s | found=True", client_id)
        else:
            logger.warning("[ClientHub] READ | client_id=%s | found=False", client_id)
        return record
