"""
LiteLLM tool definitions (OpenAI function-calling format) for Stage 1 risk assessment.

These tools allow the LLM to call simulated external services during its analysis.
The actual service calls are handled by the pipeline's tool dispatcher.

Tool use is controlled by the ENABLE_TOOLS environment variable.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.compliance_one import ComplianceOneService
    from services.sharepoint import SharePointService
    from services.outlook import OutlookService

logger = logging.getLogger(__name__)

# ── FATF jurisdiction risk reference ──────────────────────────────────────────

_JURISDICTION_RISK: dict[str, dict] = {
    # High-risk / blacklist
    "iran": {"risk_level": "high", "fatf_status": "black_list", "notes": "FATF blacklisted; subject to enhanced countermeasures"},
    "north korea": {"risk_level": "high", "fatf_status": "black_list", "notes": "FATF blacklisted; subject to enhanced countermeasures"},
    "myanmar": {"risk_level": "high", "fatf_status": "black_list", "notes": "FATF blacklisted as of 2023"},
    # Grey list examples
    "nigeria": {"risk_level": "medium", "fatf_status": "grey_list", "notes": "FATF grey list — enhanced monitoring required"},
    "pakistan": {"risk_level": "medium", "fatf_status": "grey_list", "notes": "FATF grey list — enhanced monitoring required"},
    "uae": {"risk_level": "medium", "fatf_status": "grey_list", "notes": "UAE removed from grey list 2024; residual monitoring period"},
    "cayman islands": {"risk_level": "medium", "fatf_status": "grey_list", "notes": "Offshore financial centre; enhanced scrutiny for source of funds"},
    "british virgin islands": {"risk_level": "medium", "fatf_status": "grey_list", "notes": "Offshore centre; common vehicle for opaque structures"},
    "bvi": {"risk_level": "medium", "fatf_status": "grey_list", "notes": "British Virgin Islands — offshore centre"},
    "panama": {"risk_level": "medium", "fatf_status": "grey_list", "notes": "Offshore centre; Panama Papers exposure"},
    # Low risk
    "united states": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; strong AML/CFT framework"},
    "usa": {"risk_level": "low", "fatf_status": "regular", "notes": "United States — FATF member"},
    "uk": {"risk_level": "low", "fatf_status": "regular", "notes": "United Kingdom — FATF member"},
    "united kingdom": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; strong regulatory environment"},
    "germany": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member"},
    "france": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member"},
    "canada": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member"},
    "australia": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member"},
    "singapore": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; strong MAS regulatory framework"},
    "luxembourg": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; major EU fund domicile"},
    "ireland": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; EU fund jurisdiction"},
    # Additional FATF members — common fund domiciles
    "switzerland": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; strong AML/CFT framework; FINMA regulated"},
    "hong kong": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; SFC regulated; major Asian financial centre"},
    "japan": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; FSA regulated"},
    "netherlands": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; DNB/AFM regulated; EU fund jurisdiction"},
    "sweden": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; Finansinspektionen regulated"},
    "denmark": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; Finanstilsynet regulated"},
    "new zealand": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; FMA regulated"},
    "liechtenstein": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; FMA regulated; EEA passporting rights"},
    "malta": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; MFSA regulated; EU fund jurisdiction"},
    # Crown dependencies and offshore centres — FATF-equivalent frameworks
    "isle of man": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF-equivalent framework; IOM FSA regulated; strong AML regime"},
    "jersey": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF-equivalent framework; JFSC regulated; leading offshore centre"},
    "guernsey": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF-equivalent framework; GFSC regulated"},
    "bermuda": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF-equivalent framework; BMA regulated; major reinsurance and fund domicile"},
    # Emerging market FATF members
    "india": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; SEBI/RBI regulated; enhanced source-of-funds scrutiny advised for complex structures"},
    "brazil": {"risk_level": "low", "fatf_status": "regular", "notes": "FATF member; CVM/BCB regulated; moderate AML framework maturity"},
    "mexico": {"risk_level": "medium", "fatf_status": "regular", "notes": "FATF member; CNBV regulated; elevated concern for proceeds-of-crime risk in certain sectors"},
    # Grey list — China (mainland) not a grey list but elevated monitoring recommended
    "china": {"risk_level": "medium", "fatf_status": "regular", "notes": "FATF member; PBOC/CSRC regulated; enhanced due diligence advised for state-linked entities"},
    "cyprus": {"risk_level": "medium", "fatf_status": "regular", "notes": "FATF member; EU jurisdiction; elevated scrutiny for Russian beneficial owner exposure"},
}

_DEFAULT_JURISDICTION = {
    "risk_level": "medium",
    "fatf_status": "unknown",
    "notes": "Jurisdiction not in reference database — manual assessment required",
}

# ── Tool schema definitions ───────────────────────────────────────────────────

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "check_sanctions_list",
            "description": (
                "Screen an entity or individual against OFAC SDN, UN Consolidated, EU, and FinCEN sanctions databases. "
                "Call this for any client, beneficial owner, or associated party where sanctions exposure is uncertain."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_name": {
                        "type": "string",
                        "description": "Full legal name of the entity or individual to screen",
                    },
                    "entity_type": {
                        "type": "string",
                        "enum": ["individual", "corporate", "fund", "government_entity"],
                        "description": "Classification of the entity being screened",
                    },
                    "nationality": {
                        "type": "string",
                        "description": "Country of nationality or incorporation (ISO country name or code)",
                    },
                    "date_of_birth": {
                        "type": "string",
                        "description": "Date of birth in YYYY-MM-DD format (for individuals only)",
                    },
                },
                "required": ["entity_name", "entity_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_pep_status",
            "description": (
                "Check whether an individual is a Politically Exposed Person (PEP) — "
                "a current or former government official, senior political figure, or their close associates. "
                "Call this for any individual principal, beneficial owner, or key decision-maker."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "person_name": {
                        "type": "string",
                        "description": "Full name of the individual to check",
                    },
                    "nationality": {
                        "type": "string",
                        "description": "Country of nationality",
                    },
                    "role": {
                        "type": "string",
                        "description": "Known role or position (e.g. 'General Partner', 'CEO', 'Director')",
                    },
                },
                "required": ["person_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_jurisdiction_risk",
            "description": (
                "Look up the FATF risk classification and regulatory status for a jurisdiction. "
                "Call this for any offshore, non-US, or uncertain jurisdiction mentioned in the application."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "country_name": {
                        "type": "string",
                        "description": "Full country name (e.g. 'Cayman Islands', 'United States')",
                    },
                    "country_code": {
                        "type": "string",
                        "description": "ISO 3166-1 alpha-2 country code (e.g. 'KY', 'US')",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "store_document",
            "description": "Store a client document in SharePoint. Call when a document has been received and should be stored.",
            "parameters": {
                "type": "object",
                "properties": {
                    "client_id": {"type": "string", "description": "application_id of the client"},
                    "document_name": {"type": "string", "description": "Name of the document (e.g. 'board_resolution.pdf')"},
                    "document_type": {
                        "type": "string",
                        "enum": ["board_resolution", "investment_policy_statement", "kyc_form", "source_of_funds", "beneficial_ownership", "authorization", "other"],
                        "description": "Type classification of the document",
                    },
                },
                "required": ["client_id", "document_name", "document_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_status_notification",
            "description": "Send a notification to a stakeholder about the application status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_role": {
                        "type": "string",
                        "enum": ["relationship_manager", "compliance_officer", "operations"],
                        "description": "Role of the recipient",
                    },
                    "client_id": {"type": "string", "description": "application_id of the client"},
                    "message": {"type": "string", "description": "Notification message body"},
                },
                "required": ["recipient_role", "client_id", "message"],
            },
        },
    },
]


# ── Tool dispatcher ────────────────────────────────────────────────────────────

class ToolDispatcher:
    """Resolves LLM tool calls to the appropriate simulated service method."""

    def __init__(
        self,
        compliance_service: "ComplianceOneService",
        sharepoint_service: "SharePointService",
        outlook_service: "OutlookService",
    ) -> None:
        self._compliance = compliance_service
        self._sharepoint = sharepoint_service
        self._outlook = outlook_service

    def dispatch(self, tool_name: str, tool_args: dict) -> str:
        """Execute a tool call and return the result as a JSON string."""
        try:
            result = self._execute(tool_name, tool_args)
        except Exception as exc:
            logger.error("Tool %s failed with args %s: %s", tool_name, tool_args, exc)
            result = {"error": str(exc), "tool": tool_name}
        return json.dumps(result)

    def _execute(self, tool_name: str, args: dict) -> dict:
        if tool_name == "check_sanctions_list":
            return self._compliance.check_sanctions(
                entity_name=args["entity_name"],
                entity_type=args["entity_type"],
                nationality=args.get("nationality", ""),
                date_of_birth=args.get("date_of_birth", ""),
            )
        elif tool_name == "check_pep_status":
            return self._compliance.check_pep(
                person_name=args["person_name"],
                nationality=args.get("nationality", ""),
                role=args.get("role", ""),
            )
        elif tool_name == "lookup_jurisdiction_risk":
            country = (args.get("country_name") or args.get("country_code") or "").lower().strip()
            result = _JURISDICTION_RISK.get(country, _DEFAULT_JURISDICTION)
            logger.info("[JurisdictionDB] LOOKUP | country=%s | risk=%s", country, result["risk_level"])
            return {"country": country, **result}
        elif tool_name == "store_document":
            doc_name = args.get("document_name", "")
            doc_type = args.get("document_type", "other")
            client_id = args["client_id"]
            return self._sharepoint.upload_document(
                client_id=client_id,
                document_name=doc_name,
                content=f"Stored document '{doc_name}' of type '{doc_type}' for client {client_id}",
            )
        elif tool_name == "send_status_notification":
            return self._outlook.send_role_notification(
                recipient_role=args["recipient_role"],
                client_id=args["client_id"],
                message=args["message"],
            )
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
