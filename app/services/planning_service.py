"""Create unapproved evidence-plan proposals from safe issue context."""

import asyncio
import logging
import re
from functools import lru_cache
from typing import Any

from pydantic import Field, ValidationError

from app.agent.groq_client import (
    ChatModelProvider,
    GroqChatModelProvider,
    StructuredChatModel,
)
from app.agent.prompts import build_planning_messages
from app.collectors.registry import CollectorRegistry, create_default_registry
from app.core.config import Settings, get_settings
from app.core.exceptions import AIProviderError
from app.models._common import StrictModel, validate_secret_free_text
from app.models.issue import IssueRequest
from app.models.plan import CollectorName, EvidencePlan, EvidenceStep
from app.models.scenario import ScenarioDefinition

logger = logging.getLogger(__name__)


class _EvidenceStepProposal(StrictModel):
    collector: CollectorName
    reason: str = Field(min_length=10, max_length=500)
    parameters: dict[str, object] = Field(default_factory=dict)
    required: bool = True


class _EvidencePlanProposal(StrictModel):
    issue_summary: str = Field(min_length=1, max_length=1000)
    suspected_category: str = Field(min_length=1, max_length=100)
    steps: list[_EvidenceStepProposal] = Field(min_length=1, max_length=10)
    warnings: list[str] = Field(default_factory=list, max_length=20)


_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:\\[^\r\n\"<>|?*]+|"
    r"\\\\[^\\/\r\n]+\\[^\\\r\n]+|"
    r"/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+)"
)


@lru_cache(maxsize=1)
def get_chat_model_provider() -> ChatModelProvider:
    """Return a reusable provider adapter without creating a network client."""
    return GroqChatModelProvider()


class EvidencePlanningService:
    """Produce a structured proposal; policy and approval remain separate steps."""

    def __init__(
        self,
        *,
        provider: ChatModelProvider | None = None,
        registry: CollectorRegistry | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._provider = provider or get_chat_model_provider()
        self._registry = registry or create_default_registry()
        try:
            self._settings = settings or get_settings()
        except ValidationError:
            raise AIProviderError(
                "Evidence planning provider configuration is invalid."
            ) from None

    async def create_plan(
        self,
        issue: IssueRequest,
        scenario: ScenarioDefinition,
    ) -> EvidencePlan:
        """Return a validated proposal without reading evidence or running policy."""
        try:
            collector_metadata = self._supported_collector_metadata(scenario)
            if not collector_metadata:
                raise AIProviderError(
                    "No registered collectors are available for this scenario."
                )
            _validate_prompt_context(issue, scenario)
            messages = build_planning_messages(
                issue,
                scenario,
                collector_metadata,
                self._settings.max_tool_calls,
            )
            model = self._provider.structured_model(_EvidencePlanProposal)
            result = await model.ainvoke(messages)
            plan = self._validate_result(result)
            if len(plan.steps) > self._settings.max_tool_calls:
                raise AIProviderError(
                    "The planning provider returned a plan exceeding the allowed step limit."
                )
        except AIProviderError:
            raise
        except Exception as error:
            category = _provider_failure_category(error)
            logger.warning(
                "Evidence planning provider request failed: %s (%s)",
                category,
                type(error).__name__,
            )
            raise AIProviderError(_safe_provider_message(category)) from None

        logger.info("Evidence planning proposal created with %d steps", len(plan.steps))
        return plan

    def _supported_collector_metadata(
        self,
        scenario: ScenarioDefinition,
    ) -> list[dict[str, object]]:
        supported_names = {collector.value for collector in scenario.available_collectors}
        metadata: list[dict[str, object]] = []
        for item in self._registry.metadata():
            name = item.get("name")
            if not isinstance(name, str) or name not in supported_names:
                continue
            metadata.append(
                {
                    "name": name,
                    "description": item.get("description", ""),
                    "supported_parameters": item.get("supported_parameters", []),
                    "required_parameters": item.get("required_parameters", []),
                    "parameter_limits": item.get("parameter_limits", {}),
                }
            )
        return metadata

    @staticmethod
    def _validate_result(result: Any) -> EvidencePlan:
        if result is None or result == "" or result == {}:
            raise AIProviderError("The planning provider returned an empty plan.")
        try:
            if isinstance(result, EvidencePlan):
                proposal = _EvidencePlanProposal(
                    issue_summary=result.issue_summary,
                    suspected_category=result.suspected_category,
                    steps=[
                        _EvidenceStepProposal(
                            collector=step.collector,
                            reason=step.reason,
                            parameters=step.parameters,
                            required=step.required,
                        )
                        for step in result.steps
                    ],
                    warnings=result.warnings,
                )
            else:
                proposal = _EvidencePlanProposal.model_validate(result)
            plan = EvidencePlan(
                issue_summary=proposal.issue_summary,
                suspected_category=proposal.suspected_category,
                steps=[
                    EvidenceStep(
                        collector=step.collector,
                        reason=step.reason,
                        parameters=step.parameters,
                        required=step.required,
                    )
                    for step in proposal.steps
                ],
                warnings=proposal.warnings,
            )
        except (TypeError, ValueError, ValidationError):
            logger.warning("Evidence planning provider returned invalid structured output")
            raise AIProviderError(
                "The planning provider returned an invalid evidence plan."
            ) from None

        text_fields = [
            plan.issue_summary,
            plan.suspected_category,
            *[step.reason for step in plan.steps],
            *plan.warnings,
        ]
        try:
            for value in text_fields:
                validate_secret_free_text(value)
                if _PATH_PATTERN.search(value):
                    raise ValueError("Plan text contains an absolute path.")
        except ValueError:
            logger.warning("Evidence planning provider returned unsafe plan text")
            raise AIProviderError(
                "The planning provider returned an invalid evidence plan."
            ) from None
        return plan


def _validate_prompt_context(
    issue: IssueRequest,
    scenario: ScenarioDefinition,
) -> None:
    """Reject prompt text containing credentials or absolute filesystem paths."""
    values = [
        issue.title,
        issue.description,
        issue.application_name,
        *issue.symptoms,
        scenario.title,
        scenario.description,
        scenario.issue_category,
        *scenario.expected_signals,
    ]
    try:
        for value in values:
            validate_secret_free_text(value)
            if _PATH_PATTERN.search(value):
                raise ValueError("Prompt text contains an absolute path.")
    except ValueError:
        raise AIProviderError(
            "Issue or scenario details contain information that cannot be sent to the planning provider."
        ) from None


def _provider_failure_category(error: Exception) -> str:
    name = type(error).__name__.lower()
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or "timeout" in name:
        return "timeout"
    if "auth" in name or "permission" in name:
        return "authentication"
    if "ratelimit" in name or "rate_limit" in name:
        return "rate_limit"
    if any(fragment in name for fragment in ("internalserver", "servererror", "apierror")):
        return "provider_server"
    return "provider_request"


def _safe_provider_message(category: str) -> str:
    return {
        "timeout": "The planning provider request timed out.",
        "authentication": "The planning provider rejected its configured credentials.",
        "rate_limit": "The planning provider is temporarily rate limited.",
        "provider_server": "The planning provider is temporarily unavailable.",
        "provider_request": "The planning provider could not complete the request.",
    }[category]
