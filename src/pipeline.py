"""
Core pipeline — Stage 1 (risk assessment) and Stage 2 (onboarding summary).

Both stages use LiteLLM with retry logic and strict Pydantic validation.
Stage 2 is always invoked after Stage 1 — the chain is sequential.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import litellm

from models.client import ClientApplication
from models.extraction import DocumentInput, ExtractedClientData
from models.risk import RiskAssessment
from models.onboarding import OnboardingSummary
from src.tools import TOOL_DEFINITIONS, ToolDispatcher
from src.utils import extract_json, extract_thinking

logger = logging.getLogger(__name__)

_MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
_BACKOFF_BASE = float(os.getenv("RETRY_BACKOFF_BASE", "2"))
_MODEL = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-6")
_PIPELINE_VERSION = os.getenv("PIPELINE_VERSION", "0.1.0")
_ENABLE_TOOLS = os.getenv("ENABLE_TOOLS", "true").lower() == "true"

# ── Prompt loading ─────────────────────────────────────────────────────────────

def _load_prompt(filename: str) -> str:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


_SYSTEM_PROMPT = _load_prompt("system_prompt.txt")
_STAGE0_TEMPLATE = _load_prompt("stage0_user_prompt.txt")
_STAGE1_TEMPLATE = _load_prompt("stage1_user_prompt.txt")
_STAGE2_TEMPLATE = _load_prompt("stage2_user_prompt.txt")


MAX_TOOL_ITERATIONS = 10

# ── LiteLLM call with retry ────────────────────────────────────────────────────

def _llm_call(
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    tool_dispatcher: Optional[ToolDispatcher] = None,
    context_label: str = "LLM",
) -> tuple[str, int]:
    """
    Call LiteLLM with exponential-backoff retry.
    Handles tool use loops if tools are provided.
    Returns (response_content, total_tokens_used).
    """
    total_tokens = 0

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            kwargs: dict = {
                "model": _MODEL,
                "messages": messages,
                "temperature": 0,
            }
            if tools:
                kwargs["tools"] = tools

            response = litellm.completion(**kwargs)
            total_tokens += (response.usage.total_tokens if response.usage else 0)
            msg = response.choices[0].message

            # ── Tool use loop ──────────────────────────────────────────────────
            if tools and tool_dispatcher and msg.tool_calls:
                current_messages = list(messages)
                current_messages.append(msg.model_dump() if hasattr(msg, "model_dump") else dict(msg))

                iteration = 0
                while msg.tool_calls:
                    if iteration >= MAX_TOOL_ITERATIONS:
                        logger.warning(
                            "[%s] Tool loop cap (%d) reached — using last text response",
                            context_label,
                            MAX_TOOL_ITERATIONS,
                        )
                        break
                    iteration += 1

                    tool_results = []
                    for tc in msg.tool_calls:
                        fn_name = tc.function.name
                        fn_args = json.loads(tc.function.arguments)
                        logger.info("[%s] Tool call: %s(%s)", context_label, fn_name, fn_args)
                        result_json = tool_dispatcher.dispatch(fn_name, fn_args)
                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": fn_name,
                            "content": result_json,
                        })

                    current_messages.extend(tool_results)
                    response = litellm.completion(
                        model=_MODEL,
                        messages=current_messages,
                        tools=tools,
                        temperature=0,
                    )
                    total_tokens += (response.usage.total_tokens if response.usage else 0)
                    msg = response.choices[0].message
                    if hasattr(msg, "model_dump"):
                        current_messages.append(msg.model_dump())
                    else:
                        current_messages.append(dict(msg))

                content = msg.content or ""
                return content, total_tokens

            content = msg.content or ""
            return content, total_tokens

        except Exception as exc:
            wait = _BACKOFF_BASE ** attempt
            logger.warning(
                "[%s] Attempt %d/%d failed: %s — retrying in %.0fs",
                context_label,
                attempt,
                _MAX_RETRIES,
                exc,
                wait,
            )
            if attempt == _MAX_RETRIES:
                raise
            time.sleep(wait)

    raise RuntimeError(f"{context_label}: exhausted all {_MAX_RETRIES} retry attempts")


# ── Stage 0: Document Extraction ──────────────────────────────────────────────

def run_stage0(
    documents: list[DocumentInput],
    application_id: str,
    pydantic_failures: Optional[list] = None,
    json_failures: Optional[list] = None,
) -> tuple[ExtractedClientData, float, int]:
    """
    Run Stage 0 document extraction for a set of onboarding documents.
    Returns (ExtractedClientData, latency_seconds, tokens_used).
    """
    schema = json.dumps(ExtractedClientData.model_json_schema(), indent=2)

    docs_xml = "\n\n".join([
        f'<document filename="{doc.filename}" type="{doc.document_type}">\n{doc.content}\n</document>'
        for doc in documents
    ])

    user_content = _STAGE0_TEMPLATE.format(
        application_id=application_id,
        documents_xml=docs_xml,
        extracted_data_schema=schema,
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    logger.info(
        "[Stage0] Starting document extraction | client=%s | documents=%d",
        application_id,
        len(documents),
    )
    t0 = time.time()
    raw_content, tokens = _llm_call(
        messages=messages,
        context_label=f"Stage0/{application_id}",
    )
    latency = time.time() - t0

    try:
        json_str = extract_json(raw_content)
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("[Stage0] JSON parse failure for %s: %s", application_id, exc)
        if json_failures is not None:
            json_failures.append(application_id)
        raise

    data.setdefault("metadata", {})
    data["metadata"]["processed_at"] = datetime.now(timezone.utc).isoformat()
    data["metadata"]["pipeline_stage"] = "document_extraction"
    data["metadata"]["model_used"] = _MODEL
    data["application_id"] = application_id

    try:
        extracted = ExtractedClientData.model_validate(data)
    except Exception as exc:
        logger.error("[Stage0] Pydantic validation failure for %s: %s", application_id, exc)
        if pydantic_failures is not None:
            pydantic_failures.append(application_id)
        raise

    logger.info(
        "[Stage0] Complete | client=%s | docs=%d | confidence=%.2f | warnings=%d | latency=%.1fs",
        application_id,
        len(documents),
        extracted.overall_extraction_confidence,
        len(extracted.extraction_warnings),
        latency,
    )
    return extracted, latency, tokens


# ── Stage 1: Risk Assessment ───────────────────────────────────────────────────

def run_stage1(
    application: ClientApplication,
    extracted_data: Optional[ExtractedClientData] = None,
    tool_dispatcher: Optional[ToolDispatcher] = None,
    pydantic_failures: Optional[list] = None,
    json_failures: Optional[list] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
) -> tuple[RiskAssessment, float, int]:
    """
    Run Stage 1 risk assessment for a single ClientApplication.
    Returns (RiskAssessment, latency_seconds, tokens_used).
    """
    schema = json.dumps(RiskAssessment.model_json_schema(), indent=2)
    if extracted_data is not None:
        extracted_section = (
            "\n<extracted_client_data>\n"
            "The following structured data was extracted from the client's submitted documents "
            "by an automated extraction pipeline. Each field includes a confidence score. "
            "Use this as your primary source of client data — it reflects what was actually "
            "submitted in the documents, including any gaps or low-confidence extractions that "
            "may indicate incomplete or ambiguous submissions.\n\n"
            f"{extracted_data.model_dump_json(indent=2)}\n"
            "</extracted_client_data>\n"
        )
    else:
        extracted_section = ""
    user_content = _STAGE1_TEMPLATE.format(
        client_application_json=application.model_dump_json(indent=2),
        risk_assessment_schema=schema,
        extracted_data_section=extracted_section,
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    tools = TOOL_DEFINITIONS if (_ENABLE_TOOLS and tool_dispatcher) else None

    logger.info(
        "[Stage1] Starting risk assessment | client=%s | tools=%s",
        application.application_id,
        bool(tools),
    )
    t0 = time.time()
    raw_content, tokens = _llm_call(
        messages=messages,
        tools=tools,
        tool_dispatcher=tool_dispatcher,
        context_label=f"Stage1/{application.application_id}",
    )
    latency = time.time() - t0

    thinking = extract_thinking(raw_content)
    if thinking:
        logger.info("[Stage1] Chain-of-thought:\n%s", thinking)
        if on_thinking is not None:
            on_thinking(thinking)

    # Parse and validate
    try:
        json_str = extract_json(raw_content)
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("[Stage1] JSON parse failure for %s: %s", application.application_id, exc)
        logger.debug("[Stage1] Raw content: %s", raw_content[:500])
        if json_failures is not None:
            json_failures.append(application.application_id)
        raise

    # Inject metadata if missing
    data.setdefault("metadata", {})
    data["metadata"]["processed_at"] = datetime.now(timezone.utc).isoformat()
    data["metadata"]["pipeline_stage"] = "stage_1_risk_assessment"
    data["metadata"]["model_used"] = _MODEL

    try:
        assessment = RiskAssessment.model_validate(data)
    except Exception as exc:
        logger.error("[Stage1] Pydantic validation failure for %s: %s", application.application_id, exc)
        logger.debug("[Stage1] Parsed data: %s", json.dumps(data, indent=2)[:1000])
        if pydantic_failures is not None:
            pydantic_failures.append(application.application_id)
        raise

    logger.info(
        "[Stage1] Complete | client=%s | risk=%s | track=%s | flags=%d | latency=%.1fs",
        application.application_id,
        assessment.overall_risk_level,
        assessment.recommended_review_track,
        len(assessment.compliance_flags),
        latency,
    )
    return assessment, latency, tokens


# ── Stage 2: Onboarding Summary ────────────────────────────────────────────────

def run_stage2(
    application: ClientApplication,
    risk_assessment: RiskAssessment,
    pydantic_failures: Optional[list] = None,
    json_failures: Optional[list] = None,
) -> tuple[OnboardingSummary, float, int]:
    """
    Run Stage 2 onboarding summary for a single application + risk assessment.
    Returns (OnboardingSummary, latency_seconds, tokens_used).
    """
    schema = json.dumps(OnboardingSummary.model_json_schema(), indent=2)
    user_content = _STAGE2_TEMPLATE.format(
        client_application_json=application.model_dump_json(indent=2),
        risk_assessment_json=risk_assessment.model_dump_json(indent=2),
        onboarding_summary_schema=schema,
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    logger.info("[Stage2] Generating onboarding summary | client=%s", application.application_id)
    t0 = time.time()
    raw_content, tokens = _llm_call(
        messages=messages,
        context_label=f"Stage2/{application.application_id}",
    )
    latency = time.time() - t0

    try:
        json_str = extract_json(raw_content)
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("[Stage2] JSON parse failure for %s: %s", application.application_id, exc)
        if json_failures is not None:
            json_failures.append(application.application_id)
        raise

    data.setdefault("metadata", {})
    data["metadata"]["processed_at"] = datetime.now(timezone.utc).isoformat()
    data["metadata"]["pipeline_stage"] = "stage_2_onboarding_summary"
    data["metadata"]["model_used"] = _MODEL
    data["metadata"]["risk_assessment_reference"] = application.application_id

    try:
        summary = OnboardingSummary.model_validate(data)
    except Exception as exc:
        logger.error("[Stage2] Pydantic validation failure for %s: %s", application.application_id, exc)
        if pydantic_failures is not None:
            pydantic_failures.append(application.application_id)
        raise

    logger.info(
        "[Stage2] Complete | client=%s | complexity=%s | track=%s | blockers=%d | latency=%.1fs",
        application.application_id,
        summary.complexity_level,
        summary.estimated_onboarding_track,
        len(summary.blockers),
        latency,
    )
    return summary, latency, tokens
