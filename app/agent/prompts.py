"""Focused prompts for creating bounded evidence-plan proposals."""

import json

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.models.issue import IssueRequest
from app.models.scenario import ScenarioDefinition

PLANNING_SYSTEM_PROMPT = """You propose a minimal, issue-specific diagnostic evidence plan.
Select collectors only from the supplied supported-collector list. Use only their
documented parameters and limits. Explain why each selected collector is relevant,
and do not duplicate collection steps. Keep the plan and every step in the proposed
status. Never generate commands, scripts, executable content, credentials, or
arbitrary file paths. Never request unrestricted machine, environment-variable,
directory, or credential collection. Every proposed action is subject to local
policy validation and human approval; do not claim approval. Return only the
structured evidence-plan output. Do not generate identifiers, timestamps, or
workflow metadata; these are assigned locally."""


def build_planning_messages(
    issue: IssueRequest,
    scenario: ScenarioDefinition,
    collectors: list[dict[str, object]],
    maximum_steps: int,
) -> list[BaseMessage]:
    """Build provider messages from issue context and safe scenario metadata only."""
    context = {
        "issue": {
            "title": issue.title,
            "description": issue.description,
            "application_name": issue.application_name,
            "symptoms": issue.symptoms,
        },
        "scenario_summary": {
            "title": scenario.title,
            "description": scenario.description,
            "issue_category": scenario.issue_category,
            "expected_signals": scenario.expected_signals,
        },
        "supported_collectors": collectors,
        "maximum_allowed_plan_steps": maximum_steps,
    }
    return [
        SystemMessage(content=PLANNING_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                "Create a proposed plan using only this JSON context. "
                "Do not add collectors or parameters that are not listed.\n"
                + json.dumps(context, ensure_ascii=True, separators=(",", ":"))
            )
        ),
    ]
