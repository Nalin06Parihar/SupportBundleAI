"""Focused tests for structured evidence-plan proposals."""

import asyncio
import json
from collections.abc import Sequence

import pytest
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from app.agent.groq_client import StructuredChatModel
from app.collectors.registry import create_default_registry
from app.core.config import Settings
from app.core.exceptions import AIProviderError
from app.models.issue import IssueRequest
from app.models.plan import EvidencePlan, EvidenceStep, PolicyDecisionStatus
from app.models.scenario import ScenarioDefinition
from app.scenarios.loader import ScenarioLoader
from app.security.policy import PlanPolicyValidator
from app.services.planning_service import EvidencePlanningService


class FakeStructuredModel:
    def __init__(self, result: object = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.messages: Sequence[BaseMessage] | None = None

    async def ainvoke(self, input: object) -> object:
        if isinstance(input, list) and all(
            isinstance(message, BaseMessage) for message in input
        ):
            self.messages = input
        if self.error is not None:
            raise self.error
        return self.result


class FakeProvider:
    def __init__(self, model: StructuredChatModel) -> None:
        self.model = model
        self.schema: type[BaseModel] | None = None

    def structured_model(self, schema: type[BaseModel]) -> StructuredChatModel:
        self.schema = schema
        return self.model


@pytest.fixture
def scenario() -> ScenarioDefinition:
    return ScenarioLoader().load_scenario("high-cpu-usage")


@pytest.fixture
def issue() -> IssueRequest:
    return IssueRequest(
        title="High CPU in MapleDesk",
        description="The application has sustained high CPU utilization.",
        application_name="MapleDesk",
        symptoms=["Slow response"],
    )


def make_plan() -> EvidencePlan:
    return EvidencePlan(
        issue_summary="Investigate sustained synthetic CPU usage.",
        suspected_category="resource_usage",
        steps=[
            EvidenceStep(
                collector="process_list",
                reason="Identify processes associated with high CPU usage.",
                parameters={"max_processes": 10},
            )
        ],
    )


def make_service(
    model: FakeStructuredModel,
    *,
    max_steps: int = 3,
) -> EvidencePlanningService:
    settings = Settings(
        _env_file=None,
        groq_api_key="test-secret-key",
        groq_model="test-model",
        max_tool_calls=max_steps,
    )
    return EvidencePlanningService(
        provider=FakeProvider(model),
        registry=create_default_registry(),
        settings=settings,
    )


def test_structured_response_returns_plan_and_passes_local_policy(
    issue: IssueRequest,
    scenario: ScenarioDefinition,
) -> None:
    plan = make_plan()
    service = make_service(FakeStructuredModel(plan))

    result = asyncio.run(service.create_plan(issue, scenario))

    assert isinstance(result, EvidencePlan)
    policy = PlanPolicyValidator(
        registry=create_default_registry(),
        scenario_loader=ScenarioLoader(),
    ).validate(result, scenario)
    assert policy.status == PolicyDecisionStatus.APPROVED


def test_prompt_contains_only_supported_metadata_and_configured_limit(
    issue: IssueRequest,
    scenario: ScenarioDefinition,
) -> None:
    model = FakeStructuredModel(make_plan())
    service = make_service(model, max_steps=2)

    asyncio.run(service.create_plan(issue, scenario))

    assert model.messages is not None
    prompt = "\n".join(str(message.content) for message in model.messages)
    assert issue.title in prompt
    assert "process_list" in prompt
    assert "max_processes" in prompt
    assert '"maximum_allowed_plan_steps":2' in prompt
    assert "test-secret-key" not in prompt
    context = json.loads(str(model.messages[1].content).split("\n", maxsplit=1)[1])
    supported = {item["name"] for item in context["supported_collectors"]}
    assert supported == {item.value for item in scenario.available_collectors}
    assert "evidence_files" not in context["scenario_summary"]


def test_provider_failure_is_converted_without_exposing_details(
    issue: IssueRequest,
    scenario: ScenarioDefinition,
) -> None:
    service = make_service(
        FakeStructuredModel(error=RuntimeError("sensitive provider response body"))
    )

    with pytest.raises(AIProviderError) as error:
        asyncio.run(service.create_plan(issue, scenario))

    assert "sensitive provider response body" not in str(error.value)
    assert "test-secret-key" not in str(error.value)


@pytest.mark.parametrize(
    "output",
    [
        None,
        {"issue_summary": "missing required fields"},
        {"issue_summary": "Bad plan", "steps": [{"collector": "unknown"}]},
    ],
)
def test_empty_or_invalid_structured_output_is_rejected_safely(
    issue: IssueRequest,
    scenario: ScenarioDefinition,
    output: object,
) -> None:
    service = make_service(FakeStructuredModel(output))

    with pytest.raises(AIProviderError) as error:
        asyncio.run(service.create_plan(issue, scenario))

    assert "invalid evidence plan" in str(error.value) or "empty plan" in str(error.value)
    assert "test-secret-key" not in str(error.value)


def test_missing_groq_configuration_is_reported_safely() -> None:
    from app.agent.groq_client import GroqChatModelProvider

    provider = GroqChatModelProvider(
        Settings(_env_file=None, groq_api_key=None, groq_model="test-model")
    )

    with pytest.raises(AIProviderError, match="configuration is incomplete"):
        provider.structured_model(EvidencePlan)
