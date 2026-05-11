"""
Simulated ComplianceOne service — KYC/AML screening platform.
Deterministic responses based on known risk signals in the test dataset.
In production, replace with real ComplianceOne API calls.
"""
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Deterministic screening results keyed on lowercased name fragments
_SANCTIONS_HITS: dict[str, dict] = {
    "apex global": {
        "result": "possible_match",
        "confidence": 0.62,
        "matched_list": "OFAC SDN — corporate name similarity; manual review required",
    },
    "eastbridge": {
        "result": "possible_match",
        "confidence": 0.55,
        "matched_list": "FinCEN Suspicious Activity — recently incorporated entity pattern",
    },
    "r. ashford": {
        "result": "possible_match",
        "confidence": 0.70,
        "matched_list": "OFAC SDN — partial name match; introducer-routed account",
    },
}

_PEP_HITS: dict[str, dict] = {
    "velocity venture": {
        "is_pep": True,
        "pep_category": "Former Government Official (14 months post-office)",
        "source": "PEP Database v2024-Q4",
    },
    "mercer-hastings": {
        "is_pep": True,
        "pep_category": "Corporate Insider — CEO of publicly traded company (10b5-1 implications)",
        "source": "SEC EDGAR cross-reference",
    },
}


class ComplianceOneService:
    def __init__(self) -> None:
        self._screenings: dict[str, dict] = {}

    def submit_screening(self, client_data: dict) -> dict:
        name = (client_data.get("client_name") or "").lower()
        screening_id = f"SCR-{uuid.uuid4().hex[:8].upper()}"
        timestamp = datetime.now(timezone.utc).isoformat()

        sanctions = {"result": "no_match", "confidence": 1.0, "matched_list": None}
        for fragment, hit in _SANCTIONS_HITS.items():
            if fragment in name:
                sanctions = hit
                break

        pep = {"is_pep": False, "pep_category": None, "source": "PEP Database v2024-Q4"}
        for fragment, hit in _PEP_HITS.items():
            if fragment in name:
                pep = hit
                break

        adverse_media = name in ("apex global holdings ltd", "eastbridge trading group ltd", "r. ashford (via introducer)")

        result = {
            "screening_id": screening_id,
            "status": "complete",
            "screened_at": timestamp,
            "entity_name": client_data.get("client_name"),
            "sanctions_check": sanctions,
            "pep_check": pep,
            "adverse_media_flag": adverse_media,
            "system": "ComplianceOne v7.1",
        }
        self._screenings[screening_id] = result
        logger.info(
            "[ComplianceOne] SCREEN | id=%s | entity=%s | sanctions=%s | pep=%s | adverse_media=%s",
            screening_id,
            client_data.get("client_name"),
            sanctions["result"],
            pep["is_pep"],
            adverse_media,
        )
        return result

    def get_screening_status(self, screening_id: str) -> dict:
        result = self._screenings.get(screening_id, {"status": "not_found", "screening_id": screening_id})
        logger.info("[ComplianceOne] STATUS | id=%s | status=%s", screening_id, result.get("status"))
        return result

    # ---- Tool-callable helpers ------------------------------------------------

    def check_sanctions(self, entity_name: str, entity_type: str, nationality: str = "", date_of_birth: str = "") -> dict:
        name_lower = entity_name.lower()
        result = {"result": "no_match", "confidence": 1.0, "matched_list": None}
        for fragment, hit in _SANCTIONS_HITS.items():
            if fragment in name_lower:
                result = hit
                break
        logger.info(
            "[ComplianceOne] SANCTIONS_CHECK | entity=%s | result=%s | confidence=%.2f",
            entity_name,
            result["result"],
            result["confidence"],
        )
        return {"entity_name": entity_name, "entity_type": entity_type, **result}

    def check_pep(self, person_name: str, nationality: str = "", role: str = "") -> dict:
        name_lower = person_name.lower()
        result = {"is_pep": False, "pep_category": None, "source": "PEP Database v2024-Q4"}
        for fragment, hit in _PEP_HITS.items():
            if fragment in name_lower:
                result = hit
                break
        logger.info(
            "[ComplianceOne] PEP_CHECK | entity=%s | is_pep=%s",
            person_name,
            result["is_pep"],
        )
        return {"person_name": person_name, **result}
