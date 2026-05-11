"""
Simulated Outlook email notification service.
In production, replace with Microsoft Graph /sendMail endpoint.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_ROLE_EMAILS = {
    "relationship_manager": "rm-team@crestview.com",
    "compliance_officer": "compliance@crestview.com",
    "operations": "ops@crestview.com",
    "client": "client@external.com",
}


class OutlookService:
    def __init__(self) -> None:
        self._sent: list[dict] = []

    def send_notification(self, recipient: str, subject: str, body: str) -> dict:
        to_address = _ROLE_EMAILS.get(recipient, recipient)
        record = {
            "to": to_address,
            "subject": subject,
            "body_preview": body[:120] + ("..." if len(body) > 120 else ""),
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        self._sent.append(record)
        logger.info(
            "[Outlook] SEND | to=%s | subject=%s",
            to_address,
            subject,
        )
        return {"success": True, "message_id": f"MSG-{len(self._sent):04d}", "to": to_address}

    def send_role_notification(self, recipient_role: str, client_id: str, message: str) -> dict:
        subject = f"[Crestview Onboarding] Action Required — {client_id}"
        return self.send_notification(recipient_role, subject, message)
