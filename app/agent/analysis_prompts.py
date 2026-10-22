"""Structured evidence-analysis prompts and safe external context."""

import json
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.models.analysis import ConfidenceLevel
from app.models.plan import CollectorName

ANALYSIS_SYSTEM_PROMPT = """Analyze only the approved sanitized evidence supplied by the user message.
Produce evidence-backed findings and cite supplied artifact and excerpt IDs exactly.
Separate supported findings from unverified assumptions. Identify missing evidence
using only supported collector names. Use only the supported confidence values.
Do not claim a guaranteed root cause without sufficient evidence. Never provide
shell commands, PowerShell, scripts, or executable remediation instructions.
Return only the structured analysis output. Do not generate analysis IDs,
finding IDs, workflow IDs, or timestamps; those are assigned locally. Copy
artifact and excerpt IDs exactly from the supplied approved evidence."""


def build_analysis_messages(payload: dict[str, Any]) -> list[BaseMessage]:
    """Serialize only the already-approved bounded analysis context."""
    system_prompt = (
        ANALYSIS_SYSTEM_PROMPT
        + "\nSupported collector names: "
        + ", ".join(item.value for item in CollectorName)
        + ".\nSupported confidence values: "
        + ", ".join(item.value for item in ConfidenceLevel)
        + "."
    )
    return [
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=(
                "Analyze this approved evidence context only. Do not infer or "
                "reference data outside it.\n"
                + json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            )
        ),
    ]
