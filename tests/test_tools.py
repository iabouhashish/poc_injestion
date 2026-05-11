"""Tests for LiteLLM tool definitions and the ToolDispatcher."""
from __future__ import annotations

import json

import pytest

from src.tools import TOOL_DEFINITIONS, ToolDispatcher
from services.compliance_one import ComplianceOneService
from services.sharepoint import SharePointService
from services.outlook import OutlookService


@pytest.fixture()
def dispatcher() -> ToolDispatcher:
    return ToolDispatcher(
        compliance_service=ComplianceOneService(),
        sharepoint_service=SharePointService(),
        outlook_service=OutlookService(),
    )


# ── Tool definition schema ─────────────────────────────────────────────────────

class TestToolDefinitions:
    def test_five_tools_defined(self):
        assert len(TOOL_DEFINITIONS) == 5

    def test_all_tools_have_required_keys(self):
        for tool in TOOL_DEFINITIONS:
            assert tool["type"] == "function"
            assert "function" in tool
            fn = tool["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn

    def test_expected_tool_names(self):
        names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
        assert names == {
            "check_sanctions_list",
            "check_pep_status",
            "lookup_jurisdiction_risk",
            "store_document",
            "send_status_notification",
        }

    def test_check_sanctions_required_params(self):
        sanctions = next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == "check_sanctions_list")
        required = sanctions["function"]["parameters"]["required"]
        assert "entity_name" in required
        assert "entity_type" in required

    def test_check_pep_required_params(self):
        pep = next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == "check_pep_status")
        required = pep["function"]["parameters"]["required"]
        assert "person_name" in required

    def test_lookup_jurisdiction_no_required_params(self):
        jur = next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == "lookup_jurisdiction_risk")
        # Both country_name and country_code are optional — either can be passed
        required = jur["function"]["parameters"].get("required", [])
        assert required == []

    def test_store_document_required_params(self):
        doc = next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == "store_document")
        required = doc["function"]["parameters"]["required"]
        assert "client_id" in required
        assert "document_name" in required
        assert "document_type" in required

    def test_send_status_notification_required_params(self):
        notif = next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == "send_status_notification")
        required = notif["function"]["parameters"]["required"]
        assert "recipient_role" in required
        assert "client_id" in required
        assert "message" in required

    def test_tool_definitions_are_json_serialisable(self):
        # LiteLLM will JSON-encode the tool list; ensure no un-serialisable objects
        assert json.dumps(TOOL_DEFINITIONS)


# ── ToolDispatcher ─────────────────────────────────────────────────────────────

class TestToolDispatcher:
    def test_check_sanctions_known_entity(self, dispatcher):
        result_json = dispatcher.dispatch(
            "check_sanctions_list",
            {"entity_name": "Apex Global Holdings Ltd", "entity_type": "corporate"},
        )
        result = json.loads(result_json)
        assert result["result"] == "possible_match"
        assert result["entity_name"] == "Apex Global Holdings Ltd"

    def test_check_sanctions_clean_entity(self, dispatcher):
        result_json = dispatcher.dispatch(
            "check_sanctions_list",
            {"entity_name": "Greenfield State Pension Fund", "entity_type": "fund"},
        )
        result = json.loads(result_json)
        assert result["result"] == "no_match"

    def test_check_pep_known_pep(self, dispatcher):
        result_json = dispatcher.dispatch(
            "check_pep_status",
            {"person_name": "Velocity Venture Partners LLC"},
        )
        result = json.loads(result_json)
        assert result["is_pep"] is True
        assert "Former Government Official" in result["pep_category"]

    def test_check_pep_clean_individual(self, dispatcher):
        result_json = dispatcher.dispatch(
            "check_pep_status",
            {"person_name": "Linda Vasquez"},
        )
        result = json.loads(result_json)
        assert result["is_pep"] is False

    def test_lookup_jurisdiction_known_country(self, dispatcher):
        result_json = dispatcher.dispatch(
            "lookup_jurisdiction_risk",
            {"country_name": "cayman islands"},
        )
        result = json.loads(result_json)
        assert result["risk_level"] == "medium"
        assert result["fatf_status"] == "grey_list"

    def test_lookup_jurisdiction_blacklisted(self, dispatcher):
        result_json = dispatcher.dispatch(
            "lookup_jurisdiction_risk",
            {"country_name": "iran"},
        )
        result = json.loads(result_json)
        assert result["risk_level"] == "high"
        assert result["fatf_status"] == "black_list"

    def test_lookup_jurisdiction_us(self, dispatcher):
        result_json = dispatcher.dispatch(
            "lookup_jurisdiction_risk",
            {"country_name": "united states"},
        )
        result = json.loads(result_json)
        assert result["risk_level"] == "low"

    def test_lookup_jurisdiction_unknown_returns_default(self, dispatcher):
        result_json = dispatcher.dispatch(
            "lookup_jurisdiction_risk",
            {"country_name": "atlantis"},
        )
        result = json.loads(result_json)
        assert result["fatf_status"] == "unknown"

    def test_store_document(self, dispatcher):
        result_json = dispatcher.dispatch(
            "store_document",
            {"client_id": "APP-TEST-001", "document_name": "kyc.pdf", "document_type": "kyc_form"},
        )
        result = json.loads(result_json)
        assert result["success"] is True
        assert "document_url" in result

    def test_send_status_notification(self, dispatcher):
        result_json = dispatcher.dispatch(
            "send_status_notification",
            {
                "recipient_role": "compliance_officer",
                "client_id": "APP-TEST-001",
                "message": "EDD required.",
            },
        )
        result = json.loads(result_json)
        assert result["success"] is True

    def test_unknown_tool_raises_and_returns_error_json(self, dispatcher):
        result_json = dispatcher.dispatch("nonexistent_tool", {})
        result = json.loads(result_json)
        assert "error" in result

    def test_dispatch_always_returns_json_string(self, dispatcher):
        result = dispatcher.dispatch("check_sanctions_list", {"entity_name": "X", "entity_type": "individual"})
        assert isinstance(result, str)
        json.loads(result)  # must be valid JSON
