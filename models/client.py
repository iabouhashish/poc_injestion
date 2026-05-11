from __future__ import annotations

from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


ClientType = Literal[
    "individual",
    "high_net_worth_individual",
    "pension_fund",
    "corporation",
    "trust",
    "fund_of_funds",
    "sovereign_wealth_fund",
    "endowment",
    "family_office",
    "foundation",
    "venture_capital",
    "institutional",
    "other",
]


class ClientApplication(BaseModel):
    """Raw client application as ingested from the CSV and enriched for LLM processing."""

    application_id: str = Field(..., description="Unique application identifier (e.g. APP-2026-0301)")
    client_name: str = Field(..., description="Full legal name of the client entity or individual")
    client_type: str = Field(..., description="Entity classification")
    requested_services: str = Field(..., description="Investment / wealth management services requested")
    estimated_aum: float = Field(..., description="Estimated AUM in USD (parsed from raw CSV value)")
    submission_date: str = Field(..., description="Date the application was submitted (YYYY-MM-DD or MM/DD/YYYY)")
    status: str = Field(..., description="Current application status from CRM (e.g. New, Pending Review)")
    description: str = Field(..., description="Freeform narrative from the application form — primary LLM input")

    # Optional enrichment fields (not in CSV; populated by simulated or real services)
    jurisdiction: Optional[str] = Field(None, description="Primary operating jurisdiction / country of domicile")
    beneficial_owners: Optional[list[str]] = Field(
        default_factory=list,
        description="Names of ultimate beneficial owners (UBOs) if identifiable from application",
    )
    source_of_funds: Optional[str] = Field(None, description="Stated source of investment funds")
    documents_provided: Optional[list[str]] = Field(
        default_factory=list,
        description="List of documents submitted with the application",
    )
    authorized_signatories: Optional[list[str]] = Field(
        default_factory=list,
        description="Names of individuals authorized to sign on behalf of the entity",
    )
    adverse_media_indicators: Optional[list[str]] = Field(
        default_factory=list,
        description="Pre-screening flags or adverse media signals (from intake form or initial review)",
    )
    additional_notes: Optional[str] = Field(None, description="Internal notes added during intake")

    @field_validator("client_type", mode="before")
    @classmethod
    def normalize_client_type(cls, v: object) -> str:
        if not isinstance(v, str):
            return str(v)
        normalized = v.strip().lower().replace(" ", "_").replace("-", "_")
        _VARIANT_MAP: dict[str, str] = {
            "pension_fund": "pension_fund",
            "pension": "pension_fund",
            "corporation": "corporation",
            "corporate": "corporation",
            "corp": "corporation",
            "high_net_worth_individual": "high_net_worth_individual",
            "hnwi": "high_net_worth_individual",
            "high_net_worth": "high_net_worth_individual",
            "trust": "trust",
            "fund_of_funds": "fund_of_funds",
            "fof": "fund_of_funds",
            "sovereign_wealth_fund": "sovereign_wealth_fund",
            "swf": "sovereign_wealth_fund",
            "endowment": "endowment",
            "family_office": "family_office",
            "foundation": "foundation",
            "venture_capital": "venture_capital",
            "vc": "venture_capital",
            "institutional": "institutional",
            "individual": "individual",
            "other": "other",
        }
        return _VARIANT_MAP.get(normalized, normalized)

    @field_validator("estimated_aum", mode="before")
    @classmethod
    def parse_aum(cls, v: object) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        raw = str(v).strip().replace(",", "").replace("$", "").replace(" ", "")
        multiplier = 1.0
        if raw.upper().endswith("B"):
            multiplier = 1_000_000_000
            raw = raw[:-1]
        elif raw.upper().endswith("M"):
            multiplier = 1_000_000
            raw = raw[:-1]
        elif raw.upper().endswith("K"):
            multiplier = 1_000
            raw = raw[:-1]
        return float(raw) * multiplier
