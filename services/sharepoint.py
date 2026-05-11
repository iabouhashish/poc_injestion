"""
Simulated SharePoint service — document storage.
In production, replace with Microsoft Graph API calls.
"""
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class SharePointService:
    def __init__(self) -> None:
        self._documents: dict[str, list[dict]] = {}

    def upload_document(self, client_id: str, document_name: str, content: str = "") -> dict:
        doc_id = f"DOC-{uuid.uuid4().hex[:8].upper()}"
        doc_url = f"https://crestview.sharepoint.com/clients/{client_id}/{document_name}"
        record = {
            "doc_id": doc_id,
            "client_id": client_id,
            "document_name": document_name,
            "url": doc_url,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "size_bytes": len(content.encode()),
        }
        self._documents.setdefault(client_id, []).append(record)
        logger.info(
            "[SharePoint] UPLOAD | client=%s | doc=%s | url=%s",
            client_id,
            document_name,
            doc_url,
        )
        return {"success": True, "document_url": doc_url, "doc_id": doc_id}

    def list_documents(self, client_id: str) -> list[dict]:
        docs = self._documents.get(client_id, [])
        logger.info("[SharePoint] LIST | client=%s | count=%d", client_id, len(docs))
        return docs
