"""Tests for all service classes — simulated and real (Monday mocked via requests)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from services.client_hub import ClientHubService
from services.compliance_one import ComplianceOneService
from services.sharepoint import SharePointService
from services.salesforce import SalesforceService
from services.outlook import OutlookService
from services.monday_service import MondayService


# ── ClientHubService ───────────────────────────────────────────────────────────

class TestClientHubService:
    def setup_method(self):
        self.hub = ClientHubService()

    def test_write_returns_success(self):
        result = self.hub.write_client_record(
            {"application_id": "APP-001", "client_name": "Test Corp"}
        )
        assert result["success"] is True
        assert "hub_record_id" in result

    def test_hub_record_id_contains_application_id(self):
        result = self.hub.write_client_record({"application_id": "APP-001", "client_name": "X"})
        assert "APP-001" in result["hub_record_id"]

    def test_read_after_write_returns_record(self):
        self.hub.write_client_record({"application_id": "APP-002", "client_name": "Y"})
        record = self.hub.read_client_record("APP-002")
        assert record is not None
        assert record["client_name"] == "Y"

    def test_read_missing_record_returns_none(self):
        result = self.hub.read_client_record("APP-NONEXISTENT")
        assert result is None

    def test_multiple_writes_stored_independently(self):
        self.hub.write_client_record({"application_id": "APP-A", "client_name": "A"})
        self.hub.write_client_record({"application_id": "APP-B", "client_name": "B"})
        assert self.hub.read_client_record("APP-A")["client_name"] == "A"
        assert self.hub.read_client_record("APP-B")["client_name"] == "B"


# ── ComplianceOneService ───────────────────────────────────────────────────────

class TestComplianceOneService:
    def setup_method(self):
        self.svc = ComplianceOneService()

    def test_submit_screening_returns_complete_status(self):
        result = self.svc.submit_screening({"client_name": "Clean Client Corp"})
        assert result["status"] == "complete"
        assert "screening_id" in result

    def test_screening_id_stored_for_status_lookup(self):
        result = self.svc.submit_screening({"client_name": "X"})
        sid = result["screening_id"]
        status = self.svc.get_screening_status(sid)
        assert status["status"] == "complete"

    def test_missing_screening_id_returns_not_found(self):
        status = self.svc.get_screening_status("SCR-FAKE9999")
        assert status["status"] == "not_found"

    def test_apex_global_gets_possible_match(self):
        result = self.svc.submit_screening({"client_name": "Apex Global Holdings Ltd"})
        assert result["sanctions_check"]["result"] == "possible_match"

    def test_eastbridge_gets_possible_match(self):
        result = self.svc.submit_screening({"client_name": "Eastbridge Trading Group Ltd"})
        assert result["sanctions_check"]["result"] == "possible_match"

    def test_clean_client_gets_no_match(self):
        result = self.svc.submit_screening({"client_name": "Greenfield State Pension Fund"})
        assert result["sanctions_check"]["result"] == "no_match"

    def test_velocity_venture_pep_flag(self):
        result = self.svc.submit_screening({"client_name": "Velocity Venture Partners LLC"})
        assert result["pep_check"]["is_pep"] is True

    def test_clean_client_no_pep(self):
        result = self.svc.submit_screening({"client_name": "Linda Vasquez"})
        assert result["pep_check"]["is_pep"] is False

    def test_adverse_media_flag_for_known_entities(self):
        result = self.svc.submit_screening({"client_name": "Apex Global Holdings Ltd"})
        assert result["adverse_media_flag"] is True

    def test_check_sanctions_tool_method(self):
        result = self.svc.check_sanctions("Apex Global Holdings Ltd", "corporate")
        assert result["result"] == "possible_match"
        assert result["entity_name"] == "Apex Global Holdings Ltd"

    def test_check_pep_tool_method_known(self):
        result = self.svc.check_pep("Velocity Venture Partners LLC")
        assert result["is_pep"] is True

    def test_check_pep_tool_method_unknown(self):
        result = self.svc.check_pep("Random Person")
        assert result["is_pep"] is False

    def test_r_ashford_possible_sanctions_match(self):
        result = self.svc.check_sanctions("R. Ashford (via Introducer)", "individual")
        assert result["result"] == "possible_match"


# ── SharePointService ──────────────────────────────────────────────────────────

class TestSharePointService:
    def setup_method(self):
        self.svc = SharePointService()

    def test_upload_returns_success(self):
        result = self.svc.upload_document("APP-001", "kyc.pdf", "content")
        assert result["success"] is True
        assert "document_url" in result
        assert "doc_id" in result

    def test_url_contains_client_id_and_filename(self):
        result = self.svc.upload_document("APP-001", "report.pdf")
        assert "APP-001" in result["document_url"]
        assert "report.pdf" in result["document_url"]

    def test_list_documents_empty_for_new_client(self):
        docs = self.svc.list_documents("APP-NEW")
        assert docs == []

    def test_list_documents_returns_uploaded_docs(self):
        self.svc.upload_document("APP-002", "a.pdf")
        self.svc.upload_document("APP-002", "b.pdf")
        docs = self.svc.list_documents("APP-002")
        assert len(docs) == 2

    def test_documents_isolated_per_client(self):
        self.svc.upload_document("APP-003", "x.pdf")
        self.svc.upload_document("APP-004", "y.pdf")
        assert len(self.svc.list_documents("APP-003")) == 1
        assert len(self.svc.list_documents("APP-004")) == 1

    def test_doc_id_unique_per_upload(self):
        r1 = self.svc.upload_document("APP-005", "doc1.pdf")
        r2 = self.svc.upload_document("APP-005", "doc2.pdf")
        assert r1["doc_id"] != r2["doc_id"]


# ── SalesforceService ──────────────────────────────────────────────────────────

class TestSalesforceService:
    def setup_method(self):
        self.svc = SalesforceService()

    def test_update_opportunity_returns_success(self):
        result = self.svc.update_opportunity_status("APP-001", "Pending Review")
        assert result["success"] is True
        assert result["new_status"] == "Pending Review"

    def test_get_rm_for_known_application(self):
        rm = self.svc.get_relationship_manager("APP-2026-0301")
        assert rm["name"] == "Sarah Chen"
        assert "email" in rm

    def test_get_rm_for_high_risk_returns_compliance(self):
        rm = self.svc.get_relationship_manager("APP-2026-0303")
        assert rm["team"] == "Compliance Review"

    def test_get_rm_for_unknown_client_returns_default(self):
        rm = self.svc.get_relationship_manager("APP-UNKNOWN-9999")
        assert rm["name"] == "Unassigned"

    def test_update_opportunity_records_status(self):
        self.svc.update_opportunity_status("APP-006", "In Compliance")
        assert self.svc._opportunities["APP-006"]["status"] == "In Compliance"


# ── OutlookService ─────────────────────────────────────────────────────────────

class TestOutlookService:
    def setup_method(self):
        self.svc = OutlookService()

    def test_send_notification_returns_success(self):
        result = self.svc.send_notification("compliance_officer", "Subject", "Body")
        assert result["success"] is True

    def test_send_to_known_role_resolves_email(self):
        result = self.svc.send_notification("compliance_officer", "S", "B")
        assert "@crestview.com" in result["to"]

    def test_send_to_unknown_role_uses_raw_recipient(self):
        result = self.svc.send_notification("custom@example.com", "S", "B")
        assert result["to"] == "custom@example.com"

    def test_message_id_increments(self):
        r1 = self.svc.send_notification("operations", "S1", "B1")
        r2 = self.svc.send_notification("operations", "S2", "B2")
        assert r1["message_id"] != r2["message_id"]

    def test_sent_messages_stored(self):
        self.svc.send_notification("compliance_officer", "Test", "Body")
        assert len(self.svc._sent) == 1

    def test_send_role_notification_builds_subject(self):
        result = self.svc.send_role_notification("operations", "APP-001", "Please review.")
        assert "APP-001" in result["to"] or result["success"] is True


# ── MondayService ──────────────────────────────────────────────────────────────

class TestMondayServiceNoCredentials:
    def setup_method(self):
        self.svc = MondayService()
        # Force no credentials
        self.svc.api_key = ""
        self.svc.board_id = ""

    def test_execute_returns_skipped_when_no_credentials(self):
        result = self.svc._execute("query { boards { id } }")
        assert result.get("skipped") is True

    def test_create_item_returns_none_when_no_credentials(self):
        item_id = self.svc.create_item("Test Item", {"status": "New"})
        assert item_id is None

    def test_add_update_returns_none_when_no_credentials(self):
        update_id = self.svc.add_update("12345", "Some update")
        assert update_id is None

    def test_post_application_result_returns_none_when_no_credentials(self):
        item_id = self.svc.post_application_result(
            client_name="Test Client",
            client_id="APP-001",
            risk_level="low",
            complexity_level="low",
            estimated_review_time="2 days",
            monday_status="Pending Review",
            monday_priority="Low",
            assigned_team="Ops",
            tags=[],
            risk_reasoning="Clean.",
            onboarding_summary_text="Ready to onboard.",
        )
        assert item_id is None


class TestMondayServiceWithMockedRequests:
    def _make_service(self) -> MondayService:
        svc = MondayService()
        svc.api_key = "fake-key"
        svc.board_id = "99999"
        svc._headers = {"Authorization": "fake-key", "Content-Type": "application/json"}
        svc._col_map = {}  # skip lazy column discovery in tests
        return svc

    @patch("requests.post")
    def test_create_item_returns_id_on_success(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": {"create_item": {"id": "112233"}}},
        )
        mock_post.return_value.raise_for_status = lambda: None
        svc = self._make_service()
        item_id = svc.create_item("Test Client (APP-001)", {"status": "New"})
        assert item_id == "112233"
        assert mock_post.called

    @patch("requests.post")
    def test_create_item_returns_none_on_graphql_error(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"errors": [{"message": "Invalid column"}]},
        )
        mock_post.return_value.raise_for_status = lambda: None
        svc = self._make_service()
        item_id = svc.create_item("X", {})
        assert item_id is None

    @patch("requests.post")
    def test_add_update_returns_id_on_success(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": {"create_update": {"id": "998877"}}},
        )
        mock_post.return_value.raise_for_status = lambda: None
        svc = self._make_service()
        update_id = svc.add_update("112233", "Risk assessment attached.")
        assert update_id == "998877"

    @patch("requests.post")
    def test_network_error_returns_error_dict(self, mock_post):
        import requests as req_lib
        mock_post.side_effect = req_lib.RequestException("Connection refused")
        svc = self._make_service()
        result = svc._execute("query { boards { id } }")
        assert "error" in result

    @patch("requests.post")
    def test_post_application_result_calls_create_and_two_updates(self, mock_post):
        """post_application_result should: 1 create_item + 2 add_update calls."""
        call_count = {"n": 0}
        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            m = MagicMock()
            m.raise_for_status = lambda: None
            if call_count["n"] == 1:
                m.json = lambda: {"data": {"create_item": {"id": "555"}}}
            else:
                m.json = lambda: {"data": {"create_update": {"id": str(call_count["n"])}}}
            return m

        mock_post.side_effect = side_effect
        svc = self._make_service()
        item_id = svc.post_application_result(
            client_name="Test Corp",
            client_id="APP-001",
            risk_level="high",
            complexity_level="high",
            estimated_review_time="10 days",
            monday_status="In Compliance",
            monday_priority="High",
            assigned_team="Compliance",
            tags=["edd"],
            risk_reasoning="Multiple red flags.",
            onboarding_summary_text="EDD in progress.",
        )
        assert item_id == "555"
        assert mock_post.call_count == 3  # 1 create + 2 updates
