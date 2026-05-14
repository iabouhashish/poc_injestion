"""
Real monday.com integration via GraphQL API v2.
Reads MONDAY_API_KEY and MONDAY_BOARD_ID from environment.
All failures are logged but do not block the pipeline.
"""
import json
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

MONDAY_API_URL = "https://api.monday.com/v2"


class MondayService:
    def __init__(self) -> None:
        self.api_key = os.getenv("MONDAY_API_KEY", "")
        self.board_id = os.getenv("MONDAY_BOARD_ID", "")
        self._headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
            "API-Version": "2024-01",
        }
        self._col_map: Optional[dict[str, dict]] = None  # title → {id, type}, loaded lazily
        if not self.api_key or not self.board_id:
            logger.warning(
                "[Monday] MONDAY_API_KEY or MONDAY_BOARD_ID not set — board writes will be skipped"
            )

    def _execute(self, query: str, variables: Optional[dict] = None) -> dict:
        if not self.api_key or not self.board_id:
            return {"skipped": True, "reason": "credentials not configured"}
        payload: dict = {"query": query}
        if variables:
            payload["variables"] = variables
        try:
            resp = requests.post(
                MONDAY_API_URL,
                headers=self._headers,
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if "errors" in data:
                logger.error("[Monday] GraphQL errors: %s", data["errors"])
            return data
        except requests.RequestException as exc:
            logger.error("[Monday] API request failed: %s", exc)
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Board inspection / column discovery
    # ------------------------------------------------------------------

    def get_board_columns(self) -> list[dict]:
        query = """
        query ($board_id: ID!) {
            boards(ids: [$board_id]) {
                columns { id title type }
            }
        }
        """
        result = self._execute(query, {"board_id": self.board_id})
        try:
            return result["data"]["boards"][0]["columns"]
        except (KeyError, IndexError, TypeError):
            return []

    def _load_column_map(self) -> dict[str, dict]:
        """Load board columns once, return title → {id, type} mapping."""
        columns = self.get_board_columns()
        return {col["title"]: {"id": col["id"], "type": col["type"]} for col in columns}

    def _col_id(self, title: str) -> Optional[str]:
        """Return the column ID for a display title, loading lazily."""
        if self._col_map is None:
            self._col_map = self._load_column_map()
            logger.debug("[Monday] Column map: %s", self._col_map)
        entry = self._col_map.get(title)
        return entry["id"] if entry else None

    def _col_type(self, title: str) -> Optional[str]:
        if self._col_map is None:
            self._col_map = self._load_column_map()
        entry = self._col_map.get(title)
        return entry["type"] if entry else None

    def _format_value(self, title: str, value) -> object:
        """Format a value for the monday.com column_values payload based on column type."""
        col_type = self._col_type(title)
        if col_type in ("status", "color", "dropdown"):
            return {"label": str(value)}
        if col_type == "numbers":
            return str(value)
        if col_type == "date":
            return {"date": str(value)}
        if col_type == "long_text":
            return {"text": str(value)}
        return str(value)

    def _set(self, cv: dict, title: str, value) -> None:
        """Add a column value entry to cv if the column exists on the board."""
        col_id = self._col_id(title)
        if col_id:
            cv[col_id] = self._format_value(title, value)
        else:
            logger.warning("[Monday] Column not found on board: '%s' — value not written", title)

    # ------------------------------------------------------------------
    # Item creation
    # ------------------------------------------------------------------

    def create_item(
        self,
        item_name: str,
        column_values: dict,
    ) -> Optional[str]:
        """
        Create a board item. Returns the new item ID or None on failure.
        column_values: {column_id: value_string_or_dict}
        """
        column_values_json = json.dumps(column_values)
        query = """
        mutation ($board_id: ID!, $item_name: String!, $column_values: JSON!) {
            create_item(
                board_id: $board_id
                item_name: $item_name
                column_values: $column_values
            ) {
                id
            }
        }
        """
        result = self._execute(
            query,
            {
                "board_id": self.board_id,
                "item_name": item_name,
                "column_values": column_values_json,
            },
        )
        try:
            item_id = result["data"]["create_item"]["id"]
            logger.info("[Monday] ITEM_CREATED | id=%s | name=%s", item_id, item_name)
            return item_id
        except (KeyError, TypeError):
            logger.error("[Monday] Failed to create item for '%s': %s", item_name, result)
            return None

    # ------------------------------------------------------------------
    # Updates (long-text comments on an item)
    # ------------------------------------------------------------------

    def add_update(self, item_id: str, body: str) -> Optional[str]:
        """Post a long-text update (visible in the item's Updates section)."""
        query = """
        mutation ($item_id: ID!, $body: String!) {
            create_update(item_id: $item_id, body: $body) { id }
        }
        """
        result = self._execute(query, {"item_id": item_id, "body": body})
        try:
            update_id = result["data"]["create_update"]["id"]
            logger.info("[Monday] UPDATE_ADDED | item=%s | update_id=%s", item_id, update_id)
            return update_id
        except (KeyError, TypeError):
            logger.error("[Monday] Failed to add update to item %s: %s", item_id, result)
            return None

    # ------------------------------------------------------------------
    # Item mutation (update existing items)
    # ------------------------------------------------------------------

    def update_item_columns(self, item_id: str, column_values: dict) -> bool:
        """Update column values on an existing board item. Returns True on success."""
        if not column_values:
            return True
        query = """
        mutation ($item_id: ID!, $board_id: ID!, $column_values: JSON!) {
            change_multiple_column_values(
                item_id: $item_id
                board_id: $board_id
                column_values: $column_values
            ) { id }
        }
        """
        result = self._execute(
            query,
            {
                "item_id": item_id,
                "board_id": self.board_id,
                "column_values": json.dumps(column_values),
            },
        )
        if "error" in result or "errors" in result or "skipped" in result:
            return False
        try:
            return result["data"]["change_multiple_column_values"]["id"] is not None
        except (KeyError, TypeError):
            return False

    def change_column_value(self, item_id: str, column_title: str, value) -> bool:
        """Update a single column using change_column_value — more reliable for long_text."""
        col_id = self._col_id(column_title)
        if not col_id:
            logger.warning("[Monday] Column not found on board: '%s' — value not written", column_title)
            return False
        col_type = self._col_type(column_title)
        if col_type == "long_text":
            value_json = json.dumps({"text": str(value)})
        elif col_type in ("status", "color", "dropdown"):
            value_json = json.dumps({"label": str(value)})
        elif col_type == "date":
            value_json = json.dumps({"date": str(value)})
        else:
            value_json = json.dumps(str(value))
        query = """
        mutation ($item_id: ID!, $board_id: ID!, $column_id: String!, $value: JSON!) {
            change_column_value(
                item_id: $item_id
                board_id: $board_id
                column_id: $column_id
                value: $value
            ) { id }
        }
        """
        result = self._execute(query, {
            "item_id": item_id,
            "board_id": self.board_id,
            "column_id": col_id,
            "value": value_json,
        })
        if "error" in result or "errors" in result or "skipped" in result:
            logger.warning("[Monday] change_column_value failed for '%s': %s", column_title, result)
            return False
        try:
            return result["data"]["change_column_value"]["id"] is not None
        except (KeyError, TypeError):
            logger.warning("[Monday] change_column_value unexpected response for '%s': %s", column_title, result)
            return False

    def update_item_status(
        self, item_id: str, status: str, update_text: Optional[str] = None
    ) -> None:
        """Change the Status column on an existing item and optionally post an update."""
        cv: dict = {}
        self._set(cv, "Status", status)
        if cv:
            self.update_item_columns(item_id, cv)
            logger.info("[Monday] STATUS_UPDATED | item=%s | status=%s", item_id, status)
        if update_text:
            self.add_update(item_id, update_text)

    def create_application_item(self, item_name: str, status: str = "New") -> Optional[str]:
        """Create a minimal board item with just a name and status — used at pipeline start."""
        cv: dict = {}
        self._set(cv, "Status", status)
        return self.create_item(item_name, cv)

    def populate_csv_columns(
        self,
        item_id: str,
        client_type: str,
        aum: float,
        submission_date: str,
    ) -> None:
        """Write CSV-sourced fields to the board item immediately after creation."""
        cv: dict = {}
        self._set(cv, "Client Type", client_type.replace("_", " ").title())
        if aum:
            self._set(cv, "AUM", aum)
        if submission_date:
            self._set(cv, "Submission Date", self._normalize_date(submission_date))
        if cv:
            self.update_item_columns(item_id, cv)

    def get_items_by_status(self, status: str) -> list[dict]:
        """Return all board items whose Status column matches the given label."""
        status_col_id = self._col_id("Status")
        if not status_col_id:
            logger.warning("[Monday] Could not determine Status column ID")
            return []
        query = """
        query ($board_id: ID!, $col_id: String!, $col_values: [String!]!) {
            items_page_by_column_values(
                limit: 100
                board_id: $board_id
                columns: [{column_id: $col_id, column_values: $col_values}]
            ) {
                items { id name }
            }
        }
        """
        result = self._execute(query, {
            "board_id": self.board_id,
            "col_id": status_col_id,
            "col_values": [status],
        })
        try:
            return result["data"]["items_page_by_column_values"]["items"]
        except (KeyError, TypeError):
            logger.error("[Monday] Failed to fetch items by status '%s': %s", status, result)
            return []

    def get_item_status(self, item_id: str) -> Optional[str]:
        """Return the current text value of the Status column for a board item."""
        status_col_id = self._col_id("Status")
        if not status_col_id:
            return None
        query = """
        query ($item_id: ID!) {
            items(ids: [$item_id]) {
                column_values { id text }
            }
        }
        """
        result = self._execute(query, {"item_id": item_id})
        try:
            col_values = result["data"]["items"][0]["column_values"]
            for cv in col_values:
                if cv["id"] == status_col_id:
                    return cv["text"]
            return None
        except (KeyError, IndexError, TypeError):
            logger.error("[Monday] Failed to fetch status for item %s: %s", item_id, result)
            return None

    def update_extraction_columns(
        self,
        item_id: str,
        extraction_confidence: float,
        fields_missing: list[str],
        review_reason: str = "",
    ) -> None:
        """Write extraction metadata columns to an existing board item."""
        cv: dict = {}
        self._set(cv, "Extraction Confidence", round(extraction_confidence * 100))
        if fields_missing:
            self._set(cv, "Fields Missing", ", ".join(fields_missing))
        if review_reason:
            self._set(cv, "Review Reason", review_reason)
        if cv:
            self.update_item_columns(item_id, cv)

    # ------------------------------------------------------------------
    # High-level pipeline helpers
    # ------------------------------------------------------------------

    _REVIEW_TRACK_LABELS: dict[str, str] = {
        "fast_track": "Fast Track",
        "standard": "Standard",
        "enhanced_due_diligence": "Enhanced Due Diligence",
        "manual_escalation": "Manual Escalation",
    }

    @staticmethod
    def _normalize_date(value: str) -> str:
        """Normalise MM/DD/YYYY → YYYY-MM-DD; pass YYYY-MM-DD through unchanged."""
        if "/" in value:
            parts = value.split("/")
            if len(parts) == 3:
                return f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
        return value

    def post_application_result(
        self,
        client_name: str,
        client_id: str,
        risk_level: str,
        complexity_level: str,
        estimated_review_time: str,
        monday_status: str,
        monday_priority: str,
        assigned_team: str,
        tags: list[str],
        risk_reasoning: str,
        onboarding_summary_text: str,
        review_track: str = "",
        client_type: str = "",
        aum: float = 0.0,
        submission_date: str = "",
        existing_item_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Write all compliance result columns to the board.
        If existing_item_id is provided, updates that item; otherwise creates a new one.
        """
        cv: dict = {}
        self._set(cv, "Status", monday_status)
        self._set(cv, "Priority", monday_priority)
        self._set(cv, "Risk Level", risk_level.title())
        self._set(cv, "Complexity", complexity_level.title())
        self._set(cv, "Review Track", self._REVIEW_TRACK_LABELS.get(review_track, review_track))
        self._set(cv, "Assigned Team", assigned_team)
        self._set(cv, "Estimated Review Time", estimated_review_time)
        if aum:
            self._set(cv, "AUM", aum)
        if submission_date:
            self._set(cv, "Submission Date", self._normalize_date(submission_date))

        if existing_item_id:
            self.update_item_columns(existing_item_id, cv)
            item_id = existing_item_id
        else:
            item_name = f"{client_name} ({client_id})"
            item_id = self.create_item(item_name, cv)

        if item_id:
            risk_body = (
                f"**Risk Level:** {risk_level.upper()}\n"
                f"**Complexity:** {complexity_level}\n"
                f"**Estimated Review Time:** {estimated_review_time}\n\n"
                f"---\n\n**Risk Assessment Reasoning:**\n\n{risk_reasoning}"
            )
            self.add_update(item_id, risk_body)
            self.add_update(item_id, f"**Onboarding Summary:**\n\n{onboarding_summary_text}")

        return item_id

    def post_pipeline_metrics_summary(
        self,
        board_id: str,
        metrics_text: str,
    ) -> Optional[str]:
        """
        Create a special 'Pipeline Run Summary' item on the board with aggregate metrics.
        """
        item_id = self.create_item("📊 Pipeline Run Summary", {})
        if item_id:
            self.add_update(item_id, metrics_text)
        return item_id


# ---------------------------------------------------------------------------
# Evaluation board (separate board for LLM-as-judge scores)
# ---------------------------------------------------------------------------

_EVAL_COLUMN_TITLES = [
    "Application ID",
    "Client Name",
    "Risk Assessment Score",
    "Reasoning Quality",
    "Reasoning Completeness",
    "Risk Level Appropriateness",
    "Compliance Flags Accuracy",
    "Summary Score",
    "Actionability",
    "Risk Grounding",
    "Next Steps Quality",
    "Completeness",
    "Extraction Accuracy",
    "Confidence Calibration",
    "Completeness Detection",
    "Critical Issues",
    "Processing Time",
]


class MondayEvalService:
    """Writes LLM-as-judge evaluation scores to a dedicated monday.com eval board."""

    def __init__(self) -> None:
        self.api_key = os.getenv("MONDAY_API_KEY", "")
        self.board_id = os.getenv("MONDAY_EVAL_BOARD_ID", "")
        self._headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
            "API-Version": "2024-01",
        }
        self._col_map: Optional[dict[str, str]] = None  # title → column_id, loaded lazily

        if not self.api_key or not self.board_id:
            logger.warning(
                "[MondayEval] MONDAY_API_KEY or MONDAY_EVAL_BOARD_ID not set — eval board writes will be skipped"
            )

    @property
    def _enabled(self) -> bool:
        return bool(self.api_key and self.board_id)

    def _execute(self, query: str, variables: Optional[dict] = None) -> dict:
        if not self._enabled:
            return {"skipped": True, "reason": "eval board not configured"}
        payload: dict = {"query": query}
        if variables:
            payload["variables"] = variables
        try:
            resp = requests.post(
                MONDAY_API_URL,
                headers=self._headers,
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if "errors" in data:
                logger.error("[MondayEval] GraphQL errors: %s", data["errors"])
            return data
        except requests.RequestException as exc:
            logger.error("[MondayEval] API request failed: %s", exc)
            return {"error": str(exc)}

    def _load_column_map(self) -> dict[str, str]:
        """Query board columns once and return a title → column_id mapping."""
        query = """
        query ($board_id: ID!) {
            boards(ids: [$board_id]) {
                columns { id title type }
            }
        }
        """
        result = self._execute(query, {"board_id": self.board_id})
        try:
            columns = result["data"]["boards"][0]["columns"]
            return {col["title"]: col["id"] for col in columns}
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("[MondayEval] Failed to load column map: %s", exc)
            return {}

    def _col(self, title: str) -> Optional[str]:
        """Return the column ID for a given display title, loading lazily."""
        if self._col_map is None:
            self._col_map = self._load_column_map()
        return self._col_map.get(title)

    def _create_item(self, item_name: str, column_values: dict) -> Optional[str]:
        query = """
        mutation ($board_id: ID!, $item_name: String!, $column_values: JSON!) {
            create_item(
                board_id: $board_id
                item_name: $item_name
                column_values: $column_values
            ) { id }
        }
        """
        result = self._execute(
            query,
            {
                "board_id": self.board_id,
                "item_name": item_name,
                "column_values": json.dumps(column_values),
            },
        )
        try:
            item_id = result["data"]["create_item"]["id"]
            logger.info("[MondayEval] ITEM_CREATED | id=%s | name=%s", item_id, item_name)
            return item_id
        except (KeyError, TypeError):
            logger.error("[MondayEval] Failed to create item '%s': %s", item_name, result)
            return None

    def _add_update(self, item_id: str, body: str) -> None:
        query = """
        mutation ($item_id: ID!, $body: String!) {
            create_update(item_id: $item_id, body: $body) { id }
        }
        """
        result = self._execute(query, {"item_id": item_id, "body": body})
        try:
            result["data"]["create_update"]["id"]
        except (KeyError, TypeError):
            logger.error("[MondayEval] Failed to add update to item %s: %s", item_id, result)

    def _set(self, cv: dict, title: str, value) -> None:
        """Add a column value to cv dict if the column ID is known."""
        col_id = self._col(title)
        if col_id:
            cv[col_id] = value
        else:
            logger.debug("[MondayEval] Column not found on board: '%s'", title)

    def post_evaluation_result(
        self,
        client_id: str,
        client_name: str,
        ra_eval,   # RiskAssessmentEvaluation
        os_eval,   # OnboardingSummaryEvaluation
        processing_time_seconds: float,
        ext_eval=None,  # Optional[ExtractionEvaluation]
    ) -> Optional[str]:
        """Create one item on the eval board for a single application's scores."""
        if not self._enabled:
            return None

        cv: dict = {}
        self._set(cv, "Application ID", client_id)
        self._set(cv, "Client Name", client_name)
        self._set(cv, "Risk Assessment Score", ra_eval.overall_assessment_quality.score)
        self._set(cv, "Reasoning Quality", ra_eval.reasoning_quality.score)
        self._set(cv, "Reasoning Completeness", ra_eval.reasoning_completeness.score)
        self._set(cv, "Risk Level Appropriateness", ra_eval.risk_level_appropriateness.score)
        self._set(cv, "Compliance Flags Accuracy", ra_eval.compliance_flags_accuracy.score)
        self._set(cv, "Summary Score", os_eval.overall_summary_quality.score)
        self._set(cv, "Actionability", os_eval.actionability.score)
        self._set(cv, "Risk Grounding", os_eval.risk_grounding.score)
        self._set(cv, "Next Steps Quality", os_eval.next_steps_quality.score)
        self._set(cv, "Completeness", os_eval.completeness.score)

        if ext_eval is not None:
            self._set(cv, "Extraction Accuracy", ext_eval.extraction_accuracy.score)
            self._set(cv, "Confidence Calibration", ext_eval.confidence_calibration.score)
            self._set(cv, "Completeness Detection", ext_eval.completeness_detection.score)

        critical_text = ", ".join(ra_eval.critical_issues) if ra_eval.critical_issues else "None"
        self._set(cv, "Critical Issues", critical_text)
        self._set(cv, "Processing Time", round(processing_time_seconds, 1))

        item_name = f"{client_name} ({client_id})"
        return self._create_item(item_name, cv)

    def post_eval_summary_update(
        self,
        total_processed: int,
        avg_ra_score: Optional[float],
        avg_os_score: Optional[float],
        escalation_rate: float,
        risk_distribution: dict,
        critical_issue_count: int,
        total_pipeline_time: float,
        total_tokens: int,
    ) -> Optional[str]:
        """Create a summary item on the eval board with aggregate metrics."""
        if not self._enabled:
            return None

        ra_str = f"{avg_ra_score:.1f}/100" if avg_ra_score is not None else "N/A"
        os_str = f"{avg_os_score:.1f}/100" if avg_os_score is not None else "N/A"
        risk_dist_str = ", ".join(f"{k}: {v}" for k, v in sorted(risk_distribution.items()))

        body = (
            f"**Eval Board Run Summary**\n\n"
            f"- Total applications processed: {total_processed}\n"
            f"- Avg risk assessment score: {ra_str}\n"
            f"- Avg summary score: {os_str}\n"
            f"- Escalation rate: {escalation_rate:.0%}\n"
            f"- Risk distribution: {risk_dist_str}\n"
            f"- Applications with critical issues: {critical_issue_count}\n"
            f"- Total pipeline processing time: {total_pipeline_time:.1f}s\n"
            f"- Total tokens used (est.): {total_tokens:,}\n"
        )

        item_id = self._create_item("📊 Eval Board Run Summary", {})
        if item_id:
            self._add_update(item_id, body)
        return item_id
